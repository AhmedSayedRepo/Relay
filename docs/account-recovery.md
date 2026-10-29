# Admin account recovery

Admins sign in with their email address as the username. If you forget the username or the password, there are three ways back in. Recovery codes always work. The email link works only when SMTP is set up. The server commands need a terminal on the server.

## 1. Recovery codes (no setup)

- `create-admin` prints 10 one-time codes such as `K7QF-2MXH-9RTD`. Store them offline.
- On the sign-in page, choose **Forgot username or password?** and then **Use a recovery code**. The code shows your username. You can set a new password at the same time; leave that field empty if you only need the username.
- Each code works once. **Account → Recovery codes** shows how many are left. You can create a new set after re-entering your password, and the old set stops working.
- Codes have about 60 random bits and are stored only as SHA-256 hashes.

## 2. Email reset link (optional)

Set these in `.env` and restart:

```
OPERATOR_PUBLIC_URL=https://relay.example.com/operator
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=you@gmail.com
SMTP_PASSWORD=<Gmail App Password>
SMTP_FROM=Relay <you@gmail.com>
```

- Gmail needs 2-Step Verification and an App Password (Google Account → Security → App passwords). It is free.
- Once these are set, **Email me a reset link** appears on the sign-in page. The link is single-use and expires after `PASSWORD_RESET_MINUTES` (default 15). Each account gets at most three links per hour.
- The page shows the same answer whether or not the address exists, so the form never reveals which accounts exist.

## 3. Server commands

```
python -m relay.infrastructure.cli list-admins
python -m relay.infrastructure.cli reset-password --email admin@example.com
python -m relay.infrastructure.cli recovery-codes --email admin@example.com
```

`reset-password` asks for the new password twice in the terminal. `recovery-codes` prints a fresh set and replaces the old one.

Sessions already signed in end within `ACCESS_TOKEN_EXPIRE_MINUTES` (default 15) after a reset.
