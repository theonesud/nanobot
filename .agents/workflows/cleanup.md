---
description: Clean up the code
---

Go through the codebase and create a list of opportunities to simplify the code without changing its logic. Simple code follows these rules:

No comments to the python code.
All imports at the top of the file, until circular dependency.
No assumptions.
It is the most minimal amount of code needed.
No variable or a function if not using twice. Do the functaionality inline.
No nesting things.
Error handling should not be nested until the nested handler is explicitly silencing the error or raising the error above is absolutely necessary. Be careful as nested function calls can also have error handlers - Avoid that. Try to have only one error handler at the root level. This is to avoid silent failures deep in the codebase.
There should be one, and preferably only one obvious way to do something.
Simple code is better than complex. Complex is better than complicated.
There should be one, and preferably only one obvious way to do something.

Run ruff formatter (ruff check . --fix --unsafe-fixes), vulture (remove any dead code, check manually if vulture is 60% confident) and pytest (fix any issues) after all your changes are done to test if everything is clean and stable.
