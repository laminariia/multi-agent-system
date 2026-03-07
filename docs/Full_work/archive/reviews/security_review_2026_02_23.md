# Security Code Review -- 2026-02-23

Reviewed by: Research Worker (Claude Opus 4.6)
Scope: 9 files across backend (Python/Litestar) and frontend (Remix/TypeScript)

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 1     |
| HIGH     | 5     |
| MEDIUM   | 5     |
| LOW      | 3     |
| **Total** | **14** |

---

## Finding 1 -- Token blacklist bypass when Valkey is down

- **Severity:** CRITICAL
- **File:** `/Users/awon/programming/projects/MAS/src/api/guards.py`
- **Lines:** 95-102
- **Code:**
  ```python
  try:
      is_blacklisted = await valkey.get(f"token:blacklist:{token.jti}")
      if is_blacklisted:
          return None
  except Exception:
      # If Valkey is unreachable we still allow the request through to avoid
      # total service disruption.
      logger.warning("valkey_token_check_failed", exc_info=True)
  ```
- **Issue:** When Valkey is unreachable (network partition, crash, restart), the blacklist check silently fails open. Any previously-blacklisted (logged-out) token is accepted as valid for the remainder of its TTL.
- **Impact in production:** An attacker who steals a JWT and knows the user logged out can wait for a Valkey blip (common during Railway deploys/restarts) and use the token. On Railway, Valkey restarts clear all in-memory data, so *every* blacklisted token is instantly un-blacklisted.
- **Recommended fix:**
  1. Fail closed: return `None` (deny access) when Valkey is unreachable. Add a circuit-breaker with a short retry window (e.g., 3 retries over 500ms) before denying.
  2. Alternatively, persist blacklist entries in PostgreSQL as a secondary store, and check DB when Valkey is down.
  3. At minimum, emit a metric/alert counter on Valkey failure so ops can react.

---

## Finding 2 -- Refresh token not blacklisted on rotation

- **Severity:** HIGH
- **File:** `/Users/awon/programming/projects/MAS/src/api/routes/auth.py`
- **Lines:** 207-254 (refresh endpoint)
- **Code:**
  ```python
  async def refresh(self, data: TokenRefreshSchema, db_session: AsyncSession) -> TokenRefreshResponseSchema:
      # ... decode token, verify type ...
      new_access = create_access_token(user)
      new_refresh = create_refresh_token(user)
      # OLD refresh token is NOT blacklisted here
      return TokenRefreshResponseSchema(access_token=new_access, refresh_token=new_refresh)
  ```
- **Issue:** When a refresh token is exchanged for new tokens, the old refresh token is not added to the blacklist. It remains valid until its natural expiry (7 days by default). This means a stolen refresh token can be replayed unlimited times.
- **Impact in production:** If an attacker captures a refresh token (from localStorage, logs, or network), they can generate unlimited access tokens even after the legitimate user has refreshed. The legitimate user has no way to invalidate the old refresh token short of changing `JWT_SECRET_KEY` (which invalidates ALL tokens).
- **Recommended fix:**
  1. After issuing new tokens, blacklist the old refresh token's JTI in Valkey with its remaining TTL.
  2. Consider implementing a refresh token family/chain: if a blacklisted refresh token is used, revoke the entire family (indicates token theft).
  3. Store refresh tokens in DB (not just JWT) for reliable revocation.

---

## Finding 3 -- Tokens stored in localStorage (XSS-vulnerable)

- **Severity:** HIGH
- **File:** `/Users/awon/programming/projects/MAS/dashboard/app/stores/auth-store.ts`
- **Lines:** 15-48
- **Code:**
  ```typescript
  export const useAuthStore = create<AuthState>()(
    persist(
      (set) => ({
        accessToken: null,
        refreshToken: null,
        // ...
      }),
      {
        name: "auth-storage",  // persisted to localStorage
      }
    )
  );
  ```
- **Issue:** Zustand's `persist` middleware defaults to `localStorage`. Both `accessToken` and `refreshToken` are stored there, making them readable by any JavaScript executing in the page context.
- **Impact in production:** A single XSS vulnerability (including via third-party scripts, browser extensions, or any future `dangerouslySetInnerHTML` with user data) gives an attacker both tokens. The refresh token has a 7-day lifetime, providing a long exploitation window.
- **Recommended fix:**
  1. Move refresh tokens to `httpOnly` secure cookies set by the server (Litestar response). This makes them completely inaccessible to JavaScript.
  2. Keep only the short-lived access token in memory (not localStorage) -- reconstruct it on page load using the refresh cookie.
  3. If localStorage must be used, at minimum encrypt the tokens with a per-session key and implement token binding.

---

## Finding 4 -- Fixed PBKDF2 salt for key derivation

- **Severity:** HIGH
- **File:** `/Users/awon/programming/projects/MAS/src/security/encryption.py`
- **Lines:** 37, 50-59
- **Code:**
  ```python
  _PBKDF2_SALT = b"mas-credential-encryption-salt-v1"

  def _derive_key(passphrase: str) -> bytes:
      kdf = PBKDF2HMAC(
          algorithm=hashes.SHA256(),
          length=32,
          salt=_PBKDF2_SALT,
          iterations=_PBKDF2_ITERATIONS,
      )
  ```
- **Issue:** The PBKDF2 salt is a hardcoded constant, not a random per-derivation value. Salt exists to prevent precomputation (rainbow table) attacks and to ensure identical passphrases produce different keys across installations. A fixed salt defeats both purposes.
- **Impact in production:** If two MAS deployments use the same passphrase (likely in dev/staging), they produce identical encryption keys. An attacker who extracts the salt from the source code (it is public on GitHub) can precompute a rainbow table of passphrase-to-Fernet-key mappings for common passphrases.
- **Recommended fix:**
  1. Generate a random 16-byte salt on first run and store it alongside the encrypted data or in a separate config/secret.
  2. Migration: re-encrypt existing credentials with new per-installation salt.
  3. Alternatively, require `ENCRYPTION_KEY` to always be a proper 32-byte Fernet key (skip PBKDF2 path entirely) and fail hard if it is not.

---

## Finding 5 -- `lru_cache` on Fernet instance prevents key rotation

- **Severity:** HIGH
- **File:** `/Users/awon/programming/projects/MAS/src/security/encryption.py`
- **Lines:** 62-84
- **Code:**
  ```python
  @lru_cache(maxsize=1)
  def get_fernet() -> Fernet:
      raw_key = get_settings().ENCRYPTION_KEY
      # ...
  ```
- **Issue:** `lru_cache` caches the Fernet instance for the lifetime of the process. If `ENCRYPTION_KEY` is rotated (e.g., via env var update or DB credential reload), the old key continues to be used until the process restarts. There is no `cache_clear()` mechanism exposed.
- **Impact in production:** Emergency key rotation (after a suspected compromise) requires a full process restart of all API instances. During a rolling restart window, some instances may use the old key and some the new key, causing decryption failures.
- **Recommended fix:**
  1. Replace `lru_cache` with a simple module-level `_fernet: Fernet | None = None` + a `reset_fernet()` function that clears it.
  2. Wire `reset_fernet()` into the credential reload path (`LLMClient.update_credentials` already clears its cache -- do the same here).
  3. Alternatively, if rotation is not needed, document this as an explicit design decision.

---

## Finding 6 -- Refresh token blacklist check missing

- **Severity:** HIGH
- **File:** `/Users/awon/programming/projects/MAS/src/api/routes/auth.py`
- **Lines:** 225-232
- **Code:**
  ```python
  payload = Token.decode(
      encoded_token=data.refresh_token,
      secret=settings.JWT_SECRET_KEY,
      algorithm="HS256",
  )
  ```
- **Issue:** The `/refresh` endpoint decodes the refresh token but never checks whether it has been blacklisted. Even if Finding 2 is fixed (old tokens blacklisted on rotation), this endpoint would still accept blacklisted refresh tokens.
- **Impact in production:** Combined with Finding 2, this creates a scenario where even if you add blacklisting logic on rotation, it won't be enforced because the refresh endpoint never reads the blacklist.
- **Recommended fix:** After decoding the refresh token, check `valkey.get(f"token:blacklist:{payload.jti}")` -- same pattern as in `retrieve_user_handler`.

---

## Finding 7 -- Missing Content-Security-Policy header

- **Severity:** MEDIUM
- **File:** `/Users/awon/programming/projects/MAS/src/api/main.py`
- **Lines:** 75-101
- **Issue:** `SecurityHeadersMiddleware` sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, `Permissions-Policy`, and `X-XSS-Protection` -- but does NOT include a `Content-Security-Policy` (CSP) header.
- **Impact in production:** Without CSP, browsers allow inline scripts, arbitrary `eval()`, and loading of resources from any origin. This is the primary defense against XSS after proper output encoding. Since the dashboard uses `dangerouslySetInnerHTML` (Finding 8), CSP is especially important as defense-in-depth.
- **Recommended fix:**
  1. Add a CSP header. Start with a restrictive policy:
     ```
     default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' {API_URL}; img-src 'self' data:; frame-ancestors 'none'
     ```
  2. Use nonces for inline scripts (the theme script in root.tsx).
  3. Add `report-uri` or `report-to` directive to monitor violations before enforcing.

---

## Finding 8 -- `dangerouslySetInnerHTML` with server-controlled data

- **Severity:** MEDIUM
- **File:** `/Users/awon/programming/projects/MAS/dashboard/app/root.tsx`
- **Lines:** 38, 109-113
- **Code (line 38):**
  ```tsx
  <script dangerouslySetInnerHTML={{ __html: themeScript }} />
  ```
  The `themeScript` is a static string literal -- safe.
- **Code (lines 109-113):**
  ```tsx
  <script
    dangerouslySetInnerHTML={{
      __html: `window.ENV = ${JSON.stringify(data.ENV)}`,
    }}
  />
  ```
- **Issue:** `data.ENV` comes from the server loader which reads `process.env.API_URL`. While `JSON.stringify` provides basic escaping, it does NOT escape `</script>` sequences. If `API_URL` contains `</script><script>alert(1)//`, the browser interprets it as closing the current script tag and opening a new one.
- **Impact in production:** If an attacker can influence `API_URL` (via environment injection, CI/CD misconfiguration, or supply-chain attack), they achieve arbitrary JavaScript execution for every user.
- **Recommended fix:**
  1. Sanitize the serialized JSON by replacing `</` with `<\/` and `<!--` with `<\!--`:
     ```typescript
     const safeJSON = JSON.stringify(data.ENV).replace(/</g, '\\u003c');
     ```
  2. Or use a library like `serialize-javascript` which handles all edge cases.
  3. CSP with nonces (Finding 7) provides defense-in-depth here.

---

## Finding 9 -- `os.environ` usage instead of Settings

- **Severity:** MEDIUM
- **File:** `/Users/awon/programming/projects/MAS/src/sandbox/manager.py`
- **Lines:** 43
- **Code:**
  ```python
  self._e2b_api_key = e2b_api_key or os.environ.get("E2B_API_KEY", "")
  ```
- **Issue:** Direct `os.environ` access bypasses the centralized `Settings` class (from `src/core/config.py`) which provides validation, type coercion, and production secret checks. This is explicitly called out as a pattern violation in `RULES.md` / `CLAUDE.md`.
- **Impact in production:** Inconsistent configuration loading -- the DB-first credential loader (`load_platform_credentials`) and `Settings.E2B_API_KEY` are never consulted. API keys updated via the dashboard Settings page won't be picked up by the sandbox manager.
- **Recommended fix:**
  ```python
  from src.core.config import get_settings
  self._e2b_api_key = e2b_api_key or get_settings().E2B_API_KEY
  ```

---

## Finding 10 -- API keys in URL query parameters (logged by proxies)

- **Severity:** MEDIUM
- **File:** `/Users/awon/programming/projects/MAS/src/api/routes/settings.py`
- **Lines:** 556-558, 571-573
- **Code:**
  ```python
  "gemini_api_key": (
      f"https://generativelanguage.googleapis.com/v1/models?key={key_value}",
      {},
  ),
  # ...
  "hunter_api_key": (
      f"https://api.hunter.io/v2/account?api_key={key_value}",
      {},
  ),
  ```
- **Issue:** API keys for Gemini and Hunter are placed in URL query parameters. URLs are logged by reverse proxies (Railway, nginx), browser history, CDN access logs, and potentially in Sentry breadcrumbs.
- **Impact in production:** API keys leak into Railway deployment logs, Sentry events, and any HTTP access logging configured on the infrastructure side.
- **Recommended fix:**
  1. For Gemini: use the `x-goog-api-key` header instead of `?key=` query parameter (supported since 2024).
  2. For Hunter: check if header-based auth is available; if not, add a comment acknowledging the risk and ensure logging does not capture full URLs for these requests.

---

## Finding 11 -- JWT HS256 with shared secret

- **Severity:** MEDIUM
- **File:** `/Users/awon/programming/projects/MAS/src/api/guards.py`
- **Lines:** 113-119
- **Code:**
  ```python
  jwt_auth: JWTAuth[User] = JWTAuth[User](
      # ...
      algorithm="HS256",
  )
  ```
- **Issue:** HS256 uses a symmetric shared secret. The same key signs and verifies tokens. Any service/component that needs to verify tokens must also have the ability to forge them.
- **Impact in production:** Currently the MAS is a monolith, so this is LOW risk. However, if the system scales to multiple services (e.g., separate dashboard backend, webhook processor, Telegram bot service), each service that verifies JWTs can also mint arbitrary tokens. Also, HS256 is susceptible to brute-force if the secret has low entropy.
- **Recommended fix:** This is acceptable for the current architecture. For future multi-service deployments, consider migrating to RS256 (asymmetric) where only the auth service holds the private key and other services verify with the public key. Ensure `JWT_SECRET_KEY` is at least 256 bits of entropy.

---

## Finding 12 -- `mask_value` leaks short secrets in full

- **Severity:** LOW
- **File:** `/Users/awon/programming/projects/MAS/src/security/encryption.py`
- **Lines:** 130-157
- **Code:**
  ```python
  def mask_value(value: str, visible: int = 4) -> str:
      if len(value) <= visible:
          return value  # returned UNCHANGED
  ```
- **Issue:** Secrets with 4 or fewer characters are returned completely unmasked. While most API keys are longer, some short tokens (e.g., 2FA codes, short passwords accidentally stored) would be exposed.
- **Impact in production:** Minimal for API keys (typically 20+ chars), but the credentials overview endpoint returns masked values in API responses, and this edge case could expose short secrets.
- **Recommended fix:** Always mask if the string is non-empty. For short strings, return `"****"` instead of the original value.

---

## Finding 13 -- Role disclosure in error message

- **Severity:** LOW
- **File:** `/Users/awon/programming/projects/MAS/src/api/guards.py`
- **Lines:** 147-149
- **Code:**
  ```python
  raise PermissionDeniedException(
      detail=f"Role {roles!r} required. Current role: '{user.role}'",
  )
  ```
- **Issue:** The error response reveals the user's current role AND the required roles. This is information disclosure that helps an attacker understand the authorization model and target privilege escalation.
- **Impact in production:** Low severity on its own, but combined with other vulnerabilities it helps an attacker map the system's permission model.
- **Recommended fix:** Return a generic message: `"You do not have permission to perform this action"`. Log the detailed role mismatch server-side only.

---

## Finding 14 -- Email enumeration via registration endpoint

- **Severity:** LOW
- **File:** `/Users/awon/programming/projects/MAS/src/api/routes/auth.py`
- **Lines:** 86-89
- **Code:**
  ```python
  if existing is not None:
      raise ClientException(
          detail="A user with this email already exists",
          status_code=409,
      )
  ```
- **Issue:** The registration endpoint explicitly discloses whether an email is already registered. The login endpoint correctly uses a generic "Invalid email or password" message, but registration breaks this protection.
- **Impact in production:** An attacker can enumerate valid email addresses by attempting registration. Given the 60 req/min rate limit, and that Railway collapses all IPs to one, this limits enumeration speed -- but does not prevent it.
- **Recommended fix:** Return a generic success message for all registration attempts: "If this email is not already registered, you will receive a confirmation." Alternatively, since this is an invite-only system with admin approval, this may be acceptable risk -- document the decision.

---

## Non-findings (reviewed and clean)

1. **`src/core/json_repair.py`** -- No `eval()`, `exec()`, or code injection vectors found. The module uses only `json.loads()` and regex-based string manipulation. All strategies are purely textual repair. **Clean.**

2. **`dashboard/app/root.tsx` theme script (line 31-38)** -- The `themeScript` is a static string literal defined in source code. It reads from `localStorage` client-side. No user input is interpolated. **Clean** (the ENV injection on lines 109-113 is a separate finding above).

3. **`src/api/routes/settings.py` authorization** -- All endpoints are guarded by `require_role("owner", "co_owner")` at the controller level (line 153). Ownership checks on platform accounts are present (lines 323, 389). **Clean.**

4. **Production secret validation** -- `config.py` refuses to start with default secrets when `DEBUG=False` (lines 150-156). **Good practice.**

5. **Password hashing** -- bcrypt with 12 rounds (guards.py line 40). **Adequate.**

---

## Priority remediation order

1. **Finding 1** (CRITICAL) -- Valkey blacklist fail-open. Fix immediately.
2. **Finding 2 + 6** (HIGH) -- Refresh token rotation + blacklist check. Fix together.
3. **Finding 3** (HIGH) -- Move tokens out of localStorage. Medium effort, high impact.
4. **Finding 4** (HIGH) -- Fixed PBKDF2 salt. Requires migration plan.
5. **Finding 5** (HIGH) -- lru_cache on Fernet. Quick fix.
6. **Findings 7 + 8** (MEDIUM) -- CSP + dangerouslySetInnerHTML. Deploy together.
7. **Remaining MEDIUM/LOW** -- batch in next sprint.
