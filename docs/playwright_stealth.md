# 🕵️ Playwright Stealth Configuration

**Version:** 1.0  
**Purpose:** Anti-detection browser automation for freelance platforms

---

## ⚠️ Critical for Upwork/Freelancer

Freelance platforms actively detect and ban automated browsers. This guide provides production-ready stealth techniques.

---

## 🎭 Complete Stealth Example

```python
# src/browser/stealth_browser.py
"""Production-ready stealth browser for freelance platforms."""

import asyncio
import random
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class StealthBrowser:
    """Anti-detection browser wrapper for Playwright."""
    
    # Realistic user agents (rotate monthly)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ]
    
    def __init__(
        self,
        headless: bool = False,  # IMPORTANT: headless=False is more stealthy
        proxy: Optional[dict] = None,
        session_dir: str = "./browser_sessions"
    ):
        self.headless = headless
        self.proxy = proxy
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(exist_ok=True)
        
        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
    
    async def start(self, session_name: str = "default") -> Page:
        """Start browser with anti-detection measures."""
        self._playwright = await async_playwright().start()
        
        # Launch args for stealth
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-web-security",
            "--disable-features=VizDisplayCompositor",
            "--disable-breakpad",
            f"--window-size={random.randint(1200, 1400)},{random.randint(800, 900)}",
        ]
        
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=launch_args,
        )
        
        # Persistent session for cookies/auth
        session_path = self.session_dir / session_name
        
        context_options = {
            "user_agent": random.choice(self.USER_AGENTS),
            "viewport": {"width": random.randint(1280, 1400), "height": random.randint(800, 900)},
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "geolocation": {"longitude": -73.935242, "latitude": 40.730610},  # NYC
            "permissions": ["geolocation"],
            "color_scheme": "light",
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
        }
        
        if self.proxy:
            context_options["proxy"] = self.proxy
        
        # Use persistent context for session storage
        self._context = await self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(session_path),
            headless=self.headless,
            args=launch_args,
            **context_options
        )
        
        page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        
        # Inject anti-detection scripts
        await self._inject_stealth_scripts(page)
        
        return page
    
    async def _inject_stealth_scripts(self, page: Page):
        """Inject JavaScript to evade detection."""
        
        # Remove webdriver flag
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
        """)
        
        # Mock plugins (headless has none)
        await page.add_init_script("""
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ],
            });
        """)
        
        # Mock languages
        await page.add_init_script("""
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
        """)
        
        # Hide automation indicators
        await page.add_init_script("""
            // Remove Playwright indicators
            delete window.__playwright;
            delete window.__pw_manual;
            
            // Chrome runtime mock
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {},
            };
            
            // Permissions API mock
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
        """)
    
    async def human_like_scroll(self, page: Page, duration: float = 2.0):
        """Simulate human scrolling behavior."""
        scroll_amount = random.randint(200, 400)
        steps = random.randint(3, 6)
        
        for _ in range(steps):
            await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
            await asyncio.sleep(random.uniform(0.3, 0.8))
    
    async def human_like_click(self, page: Page, selector: str):
        """Click with human-like mouse movement."""
        element = await page.wait_for_selector(selector)
        box = await element.bounding_box()
        
        if box:
            # Add random offset within element
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            
            # Move mouse with slight delay
            await page.mouse.move(x, y, steps=random.randint(5, 10))
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await page.mouse.click(x, y)
    
    async def human_like_type(self, page: Page, selector: str, text: str):
        """Type with realistic delays between keystrokes."""
        await page.click(selector)
        
        for char in text:
            await page.keyboard.type(char, delay=random.randint(50, 150))
            
            # Occasional longer pauses
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.2, 0.5))
    
    async def close(self):
        """Cleanup browser resources."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()


# Usage example
async def example_upwork_login():
    """Example: Login to Upwork with stealth browser."""
    
    # Use residential proxy for best results
    proxy = {
        "server": "http://brd.superproxy.io:22225",
        "username": "your_brightdata_user",
        "password": "your_brightdata_pass",
    }
    
    browser = StealthBrowser(
        headless=False,  # ALWAYS use non-headless for Upwork
        proxy=proxy,
        session_dir="./sessions/upwork"
    )
    
    try:
        page = await browser.start(session_name="upwork_account_1")
        
        # Navigate with delay
        await page.goto("https://www.upwork.com/ab/account-security/login")
        await asyncio.sleep(random.uniform(2, 4))
        
        # Human-like interactions
        await browser.human_like_scroll(page)
        await browser.human_like_type(page, "#login_username", "your@email.com")
        await browser.human_like_click(page, "#login_password_continue")
        
        # Wait for navigation
        await page.wait_for_load_state("networkidle")
        
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(example_upwork_login())
```

---

## 🛡️ Anti-Detection Checklist

| Technique | Status | Notes |
|-----------|--------|-------|
| `headless=False` | ✅ Required | Headless is easily detected |
| Remove `navigator.webdriver` | ✅ Implemented | Playwright sets this to `true` |
| Realistic viewport | ✅ Implemented | Random sizes 1280-1400px |
| Plugins mock | ✅ Implemented | Headless has empty plugins |
| Residential proxy | ⚠️ Required | Datacenter IPs are flagged |
| Session persistence | ✅ Implemented | Reuse cookies/auth |
| Human-like delays | ✅ Implemented | Random typing/scrolling |
| Timezone/Geolocation | ✅ Implemented | Match proxy location |

---

## 🌐 Recommended Proxy Providers

| Provider | Type | Cost | Best For |
|----------|------|------|----------|
| **BrightData** | Residential | ~$15/GB | Upwork, high-security sites |
| **Oxylabs** | Residential | ~$12/GB | General automation |
| **SmartProxy** | Datacenter | ~$5/GB | Low-security sites |

> [!WARNING]
> **Never use datacenter proxies for Upwork/Freelancer!** They will be detected immediately.

---

## 📋 Session Management

### Cookie Persistence

Browser sessions (cookies, localStorage) are stored in Valkey (Redis-compatible) with encryption:

```python
import json
import base64
from cryptography.fernet import Fernet

class CookiePersistence:
    """Encrypt and store browser cookies in Valkey."""

    def __init__(self, valkey_client, encryption_key: str):
        self.redis = valkey_client
        self.fernet = Fernet(encryption_key.encode())

    async def save_cookies(self, session_name: str, cookies: list[dict]):
        """Save encrypted cookies to Valkey."""
        data = json.dumps(cookies).encode()
        encrypted = self.fernet.encrypt(data)
        await self.redis.setex(
            f"browser:cookies:{session_name}",
            86400 * 7,  # 7 days TTL
            encrypted,
        )

    async def load_cookies(self, session_name: str) -> list[dict]:
        """Load and decrypt cookies from Valkey."""
        encrypted = await self.redis.get(f"browser:cookies:{session_name}")
        if not encrypted:
            return []
        data = self.fernet.decrypt(encrypted)
        return json.loads(data)

    async def clear_cookies(self, session_name: str):
        """Remove session cookies (e.g., after ban detection)."""
        await self.redis.delete(f"browser:cookies:{session_name}")
```

### Session Rotation

```python
import random
from datetime import datetime, timedelta

class SessionRotator:
    """Rotate browser sessions to avoid detection patterns."""

    def __init__(self, valkey_client):
        self.redis = valkey_client

    # Session pool per platform
    SESSIONS = {
        "freelancer": ["freelancer_main", "freelancer_backup"],
        "upwork": ["upwork_main", "upwork_backup"],  # OPTIONAL
        "kwork": ["kwork_main"],
    }

    async def get_session(self, platform: str) -> str:
        """Get least-recently-used session for platform."""
        sessions = self.SESSIONS.get(platform, [])
        if not sessions:
            raise ValueError(f"No sessions configured for {platform}")

        # Find session with oldest last_used timestamp
        best_session = None
        oldest_time = datetime.max

        for session_name in sessions:
            last_used = await self.redis.get(f"session:last_used:{session_name}")
            if last_used is None:
                return session_name  # Never used, pick this one
            used_at = datetime.fromisoformat(last_used.decode())
            if used_at < oldest_time:
                oldest_time = used_at
                best_session = session_name

        # Mark as used
        await self.redis.set(
            f"session:last_used:{best_session}",
            datetime.now().isoformat(),
        )
        return best_session

    async def mark_compromised(self, session_name: str):
        """Mark session as compromised (ban detected)."""
        await self.redis.set(f"session:status:{session_name}", "compromised")
        # Don't use for 48 hours
        await self.redis.setex(
            f"session:cooldown:{session_name}", 48 * 3600, "1"
        )
```

### Multi-Account Isolation

```python
class AccountIsolation:
    """Ensure each platform account uses isolated browser context."""

    # Each account gets its own:
    # 1. Browser profile (cookies, localStorage)
    # 2. Proxy IP
    # 3. User-Agent
    # 4. Viewport dimensions
    # 5. Timezone (matching proxy location)

    ACCOUNT_PROFILES = {
        "freelancer_main": {
            "proxy": {"server": "http://proxy1.example.com:22225"},
            "timezone": "America/New_York",
            "viewport": {"width": 1920, "height": 1080},
            "user_agent_index": 0,
        },
        "freelancer_backup": {
            "proxy": {"server": "http://proxy2.example.com:22225"},
            "timezone": "Europe/London",
            "viewport": {"width": 1440, "height": 900},
            "user_agent_index": 1,
        },
    }

    @classmethod
    def get_profile(cls, account_name: str) -> dict:
        """Get isolated profile for account."""
        profile = cls.ACCOUNT_PROFILES.get(account_name)
        if not profile:
            raise ValueError(f"No profile for account: {account_name}")
        return profile
```

### Security Notes

- All cookies are encrypted at rest using Fernet (AES-128-CBC)
- Encryption key stored in `ENCRYPTION_KEY` env var
- Sessions are TTL-limited (7 days max)
- Compromised sessions are automatically cooled down for 48 hours
- Never share proxy IPs between different platform accounts

---

## ⏰ Rate Limiting Best Practices

| Action | Min Delay | Max Delay |
|--------|-----------|-----------|
| Page navigation | 2s | 5s |
| Form submission | 1s | 3s |
| Between keystrokes | 50ms | 150ms |
| Scroll actions | 300ms | 800ms |
| Between bid submissions | 5min | 15min |

---

## 🚨 Emergency Procedures

If you detect a ban or CAPTCHA:

1. **Stop all automation immediately**
2. **Rotate to a different session**
3. **Switch proxy IP**
4. **Wait 24-48 hours before resuming**
5. **Consider manual verification once**

---

## 📁 Project Integration

```
src/
├── browser/
│   ├── __init__.py
│   ├── stealth_browser.py    # This file
│   ├── session_manager.py    # Session rotation
│   └── captcha_solver.py     # 2captcha integration (optional)
```
