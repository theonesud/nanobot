---
description: Clean up the code
---

Go through the codebase and create a list of opportunities to simplify the code without changing its logic. 
Check if the code is following these rules:
Variables and functions should not be created unless used atleast twice, do the functionality inline if used only once. 
Simple code is better than complex. Complex is better than complicated. 
Flat structure is better than nested.
Error handling should not be nested until the nested handler is explicitly silencing the error or raising the error above is absolutely necessary. Be careful as nested function calls can also have error handlers - Avoid that. Try to have only one error handler at the root level. This is to avoid silent failures deep in the codebase.
There should be one, and preferably only one obvious way to do something.
Do not use code comments. The code should be self explainatory. Only if there's some code that's too complex that it needs to be explained, use comments.