## Tools
claude code via antigravity for planning and codex for code generation and debugging Used it for all the phases.

## What AI got wrong
the Ai initially wrote the post endpoint using async def with await request.body() which caused the testClient to hang on Windows due to a socket.socketpair() issue in the sandbox. I noticed it because the test timed out. The fix was to use def (sync) with the body parsed via Pydantic directly from the payload parameter.

## In my own words
1. Why does a transfer to someone else's account return 404 and not 403?

- to secure the users data information if you return 403 it means that the user is not authorized to perform the action but in this case the user is authorized but the account is not found so it will lead to account enumeration attacks

2. What would happen to a customer if your idempotency record were written in a separate transaction after the transfer?

- if the transfer money is successful in a transaction and the idempotency record is written in a separate transaction if the database server crashes or the seconed transaction failed the money will be deducted from the users account but the idempotency record will not be written so it will lead to duplicate transactions