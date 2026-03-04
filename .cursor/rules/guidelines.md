---
trigger: always_on
---

We use uv for package management. do 'source .venv/bin/activate' before running any python stuff.
Do not add comments to the python code.
Keep all imports at the top of the file, until circular dependency.
Do not assume things.
Write the most minimal amount of code needed.
Do not create a variable or a function if not using twice. Do the functaionality inline.
Avoid nesting things.
Error handling should not be nested until the nested handler is explicitly silencing the error or raising the error above is absolutely necessary. Be careful as nested function calls can also have error handlers - Avoid that. Try to have only one error handler at the root level. This is to avoid silent failures deep in the codebase.
There should be one, and preferably only one obvious way to do something.
Run ruff (using 'ruff check . --fix --unsafe-fixes'), vulture (using 'vulture nanobot', remove any dead code, check manually if vulture is 60% confident) and pytest (and fix any issues) after all your changes are done to test if everything is clean and stable.