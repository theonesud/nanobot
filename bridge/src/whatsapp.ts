/**
 * WhatsApp client wrapper using Baileys.
 * Based on OpenClaw's working implementation.
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
import makeWASocket, {
  DisconnectReason,
  useMultiFileAuthState,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  downloadContentFromMessage,
  proto,
} from '@whiskeysockets/baileys';

import { Boom } from '@hapi/boom';
import qrcode from 'qrcode-terminal';
import pino from 'pino';

const VERSION = '0.1.0';

export interface InboundMessage {
  id: string;
  sender: string;
  pn: string;
  content: string;
  timestamp: number;
  isGroup: boolean;
}

export interface WhatsAppClientOptions {
  authDir: string;
  onMessage: (msg: InboundMessage) => void;
  onQR: (qr: string) => void;
  onStatus: (status: string) => void;
}

export class WhatsAppClient {
  private sock: any = null;
  private options: WhatsAppClientOptions;
  private reconnecting = false;

  constructor(options: WhatsAppClientOptions) {
    this.options = options;
  }

  async connect(): Promise<void> {
    const logger = pino({ level: 'silent' });
    const { state, saveCreds } = await useMultiFileAuthState(this.options.authDir);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`Using Baileys version: ${version.join('.')}`);

    // Create socket following OpenClaw's pattern
    this.sock = makeWASocket({
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, logger),
      },
      version,
      logger,
      printQRInTerminal: false,
      browser: ['nanobot', 'cli', VERSION],
      syncFullHistory: false,
      markOnlineOnConnect: false,
    });

    // Handle WebSocket errors
    if (this.sock.ws && typeof this.sock.ws.on === 'function') {
      this.sock.ws.on('error', (err: Error) => {
        console.error('WebSocket error:', err.message);
      });
    }

    // Handle connection updates
    this.sock.ev.on('connection.update', async (update: any) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        // Display QR code in terminal
        console.log('\n📱 Scan this QR code with WhatsApp (Linked Devices):\n');
        qrcode.generate(qr, { small: true });
        this.options.onQR(qr);
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(`Connection closed. Status: ${statusCode}, Will reconnect: ${shouldReconnect}`);
        this.options.onStatus('disconnected');

        if (shouldReconnect && !this.reconnecting) {
          this.reconnecting = true;
          // Remove old event listeners before reconnecting
          if (this.sock) {
            this.sock.ev.removeAllListeners();
          }
          console.log('Reconnecting in 5 seconds...');
          setTimeout(() => {
            this.reconnecting = false;
            this.connect();
          }, 5000);
        }
      } else if (connection === 'open') {
        console.log('✅ Connected to WhatsApp');
        this.options.onStatus('connected');
        if (this.sock.user) {
          this.options.onStatus(`me:${this.sock.user.id}`);
        }
      }
    });

    // Save credentials on update
    this.sock.ev.on('creds.update', saveCreds);

    // Handle incoming messages
    this.sock.ev.on('messages.upsert', async ({ messages, type }: { messages: any[]; type: string }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        // Skip status updates
        if (msg.key.remoteJid === 'status@broadcast') continue;

        const { content, media } = await this.extractInbound(msg);
        if (!content && (!media || media.length === 0)) continue;
        const isGroup = msg.key.remoteJid?.endsWith('@g.us') || false;
        this.options.onMessage({
          id: msg.key.id || '',
          sender: msg.key.remoteJid || '',
          content,
          media,
          timestamp: msg.messageTimestamp as number,
          isGroup,
          fromMe: msg.key.fromMe || false,
        } as any);
      }
    });
  }

  private async extractInbound(msg: any): Promise<{ content: string; media?: string[] }> {
    const message = msg.message;
    if (!message) return { content: '' };
    let content = '';
    const media: string[] = [];

    if (message.conversation) content = message.conversation;
    if (message.extendedTextMessage?.text) content = message.extendedTextMessage.text;
    if (message.imageMessage) {
      content = message.imageMessage.caption || '';
      const data = await this.downloadMedia(message.imageMessage, 'image');
      if (data) media.push(`data:image/jpeg;base64,${data.toString('base64')}`);
    }
    if (message.videoMessage) content = message.videoMessage.caption || '';
    if (message.documentMessage) content = message.documentMessage.caption || '';
    return { content, media };
  }

  private async downloadMedia(msg: any, type: 'image' | 'video' | 'audio'): Promise<Buffer | null> {
    try {
      const stream = await downloadContentFromMessage(msg, type);
      const chunks: Buffer[] = [];
      for await (const chunk of stream) {
        chunks.push(chunk);
      }
      return Buffer.concat(chunks);
    } catch {
      return null;
    }
  }

  async sendMessage(to: string, text: string): Promise<void> {
    if (!this.sock) {
      throw new Error('Not connected');
    }

    await this.sock.sendMessage(to, { text });
  }

  async disconnect(): Promise<void> {
    if (this.sock) {
      this.sock.end(undefined);
      this.sock = null;
    }
  }
}
