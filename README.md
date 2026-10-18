# Little Reader AI | القارئ الصغير

An Arabic-first, Dockerized personalized storybook platform. The original uploaded static HTML visual presentation is preserved **verbatim** in `frontend/index.html` at `/presentation`. The functional parent/child experience is at `/app`, styled to match the presentation's purple / coral / mint palette.

## Run

1. `cp .env.example .env` (on Windows PowerShell: `Copy-Item .env.example .env`).
2. Set `OPENAI_API_KEY` and generate a strong `JWT_SECRET` (`python -c "import secrets;print(secrets.token_hex(32))"`). **Never commit your .env file.**
3. `docker compose up --build`.
4. Visit **http://localhost:8080/app**; original design is **http://localhost:8080/presentation**. API documentation is at **http://localhost:8080/api/docs**.

## Parent flow

Register (parent email/password) → Add a child (name, age, interests, language) → optionally upload a child's photo after explicit parental consent → OpenAI image editing returns a cartoon avatar → choose story genre, value, setting, style and page count → OpenAI writes an age-appropriate JSON story and renders cover art based on the cartoon avatar → read the story, choose an interactive branch, use browser speech synthesis → answer comprehension quiz → read progress dashboard.

## Architecture

- Frontend: no-build vanilla HTML/CSS/JS interface, session JWT held in sessionStorage, private images retrieved using bearer tokens. No external frontend framework necessary. Presentation source preserved intact.
- Backend: Python FastAPI, SQLAlchemy, SQLite persisted in Docker volume. Parent-scoped child/story/media APIs, Argon2 password hashes, expiring JWT, Pydantic validation.
- OpenAI: `chat/completions` for structured story JSON; `images/edits` for child's cartoon likeness and cover (when avatar available), `images/generations` for new illustrations without a photo. Calls are server-side only. One full cover illustration per story; per-page illustrations are not yet provided. API usage is billable.
- Nginx: forwards `/api/` to FastAPI and serves the frontend and preserved visual presentation.

## Security notes

This is a prototype. It includes parent-account isolation, consent confirmation, bounded image upload, scoped protected image routes, authentication, server-held API credentials, and deletion endpoints. **Before public deployment with children's photos**, implement verified parent consent, formal privacy policy and retention/deletion schedule, external content moderation, LLM response schema validation with strict models, abuse/rate limiting, production monitoring, TLS, password reset/email verification, bot defenses, and independent penetration testing. Local SQLite is intended for a single-node prototype. The photo is sent to OpenAI for processing; review applicable vendor terms and legal requirements. Browser TTS quality and Arabic availability depend on the local browser/device.

## Current product boundaries

No payment, printing, school admin, password reset, production-grade content moderation, or real scoring of pronunciation. Progress scores measure quiz accuracy only; do not use them to diagnose learning differences. Stories use one cover picture plus an optional avatar, not per-page AI art.