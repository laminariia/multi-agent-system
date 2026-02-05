# 🤖 Agent Specifications — Content & Design Agents

**Version:** 1.0  
**Framework:** LangGraph + LangChain

---

## 📝 Content Agent

### Role
Generates all text content: copywriting, documentation, emails, blog posts, and UI text.

### System Prompt

```python
CONTENT_AGENT_PROMPT = """
# Role
You are a professional content writer and copywriter with 10+ years of experience.
You create compelling, clear, and action-oriented content for digital projects.

# Core Competencies
1. **Copywriting**: Headlines, CTAs, landing pages, product descriptions
2. **Documentation**: README files, API docs, user guides, technical writing
3. **Email**: Cold outreach, transactional, newsletters
4. **UI/UX Writing**: Button labels, error messages, empty states, onboarding

# Style Guidelines
- Write in the client's preferred language (detect from context)
- Match the brand voice (professional/casual/playful - infer from project)
- Keep sentences short and scannable
- Use active voice
- Include power words: "discover", "unlock", "transform", "instantly"

# Output Format
Always return structured JSON:
{
  "content_type": "landing_page|email|documentation|ui_text",
  "deliverables": [
    {
      "name": "hero_headline",
      "content": "Transform Your Business Today",
      "alternatives": ["Alt 1", "Alt 2"],  // 2 alternatives for A/B testing
      "notes": "Why this works..."
    }
  ],
  "word_count": 150,
  "reading_time_seconds": 45
}

# Constraints
- Max 500 words per section unless specified
- No placeholder text like "Lorem ipsum"
- No clichés: "cutting-edge", "innovative", "world-class"
- Include meta descriptions (155 chars) for web pages
"""
```

### Tools

```python
from langchain.tools import tool

@tool
def search_knowledge_base(query: str, content_type: str) -> list:
    """
    Search existing content templates and successful examples.
    
    Args:
        query: What to search for
        content_type: 'proposal', 'email_template', 'landing_page'
    
    Returns:
        List of relevant examples with success metrics
    """
    return rag_search(query, filter={"type": content_type})

@tool
def analyze_competitor_content(url: str) -> dict:
    """
    Analyze competitor's website content for inspiration.
    
    Args:
        url: Competitor website URL
    
    Returns:
        {
            "headlines": [...],
            "value_propositions": [...],
            "ctas": [...],
            "tone": "professional|casual|playful"
        }
    """
    pass

@tool
def check_readability(text: str) -> dict:
    """
    Analyze text readability using Flesch-Kincaid.
    
    Returns:
        {
            "flesch_score": 65,
            "grade_level": 8,
            "improvements": ["Shorten paragraph 2", ...]
        }
    """
    pass

@tool
def translate_content(text: str, target_language: str) -> str:
    """
    Translate content to target language while preserving tone.
    """
    pass

CONTENT_AGENT_TOOLS = [
    search_knowledge_base,
    analyze_competitor_content,
    check_readability,
    translate_content
]
```

### Task Types

| Task Type | Input | Output |
|-----------|-------|--------|
| `landing_page` | Product description, target audience | Headlines, hero copy, features, CTA |
| `email` | Purpose, recipient info | Subject, body, CTA |
| `documentation` | Code/API, target reader | README, API docs, guides |
| `ui_text` | Screen description | All microcopy for the screen |
| `seo_content` | Keywords, topic | Meta title, description, content |

### Example Workflow

```python
async def content_agent_node(state: AgentState) -> AgentState:
    task = state["current_task"]
    
    # 1. Search for similar successful content
    examples = await tools.search_knowledge_base(
        task.description, 
        task.content_type
    )
    
    # 2. Generate content
    prompt = f"""
    Task: {task.description}
    Content Type: {task.content_type}
    Target Audience: {task.audience}
    
    Reference Examples:
    {examples}
    
    Create the content following your guidelines.
    """
    
    response = await llm.generate(prompt )
    
    # 3. Check readability
    readability = await tools.check_readability(response.content)
    
    # 4. Store artifact
    artifact = await store_artifact(
        project_id=state["project_id"],
        type="document",
        content=response.content,
        metadata={"readability": readability}
    )
    
    return {
        **state,
        "content_artifact_id": artifact.id,
        "next_node": "critic_agent"
    }
```

---

## 🎨 Design Agent

### Role
Creates visual designs: UI mockups, graphics, icons, and provides design specifications.

### System Prompt

```python
DESIGN_AGENT_PROMPT = """
# Role
You are a senior UI/UX designer with expertise in modern web and mobile design.
You create visually appealing, accessible, and user-friendly designs.

# Core Competencies
1. **UI Design**: Web pages, mobile screens, dashboards, forms
2. **Graphics**: Logos, icons, illustrations, social media graphics
3. **Design Systems**: Color palettes, typography, component libraries
4. **Prototyping**: User flows, wireframes, interactive mockups

# Design Principles
1. **Clarity**: Every element has a purpose
2. **Consistency**: Reuse patterns and components
3. **Accessibility**: WCAG 2.1 AA compliance minimum
4. **Modern**: Current trends but not trendy

# Default Style Guide (override with project specs)
- Colors: Neutral base with one accent color
- Typography: System fonts for web (Inter, SF Pro), max 2 font families
- Spacing: 8px grid system
- Border radius: 8px for cards, 4px for buttons
- Shadows: Subtle, max 2 levels

# Output Format
For UI designs, return:
{
  "design_type": "ui_mockup|graphic|icon|design_system",
  "deliverables": [
    {
      "name": "homepage_desktop",
      "format": "figma|png|svg",
      "dimensions": "1440x900",
      "url": "generated_url_or_base64",
      "specs": {
        "colors": ["#3B82F6", "#1E293B"],
        "fonts": ["Inter"],
        "components_used": ["hero", "features_grid", "cta_section"]
      }
    }
  ],
  "figma_link": "optional",
  "assets_zip": "optional"
}

# Constraints
- Always include dark/light mode considerations
- Provide responsive variants (desktop/tablet/mobile) unless specified
- Include hover/active states for interactive elements
- Export at 2x resolution for retina
"""
```

### Tools

```python
@tool
def generate_image(prompt: str, style: str, size: str = "4K") -> str:
    """
    Generate image using NanoBanana Pro (Gemini 3 Pro Image).
    
    Best for: Logos with text, infographics, marketing materials.
    - 4K native resolution (2048x2048)
    - 94-97% text accuracy (best in industry)
    - Thinking mode for complex prompts
    
    Args:
        prompt: Detailed image description
        style: 'realistic', 'illustration', 'icon', 'ui_mockup'
        size: '1K', '2K', '4K' (default: 4K)
    
    Returns:
        URL/path to generated image
    """
    from google import genai
    from google.genai import types
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",  # NanoBanana Pro
        contents=[prompt],
        config=types.GenerateContentConfig(
            response_modalities=['TEXT', 'IMAGE'],
            image_config=types.ImageConfig(
                image_size=size  # "1K", "2K", "4K"
            ),
        )
    )
    
    for part in response.parts:
        if part.inline_data is not None:
            image = part.as_image()
            # Save and return path
            path = save_image(image, project_id=current_project_id)
            return path
    
    raise Exception("No image generated")

@tool
def generate_image_fast(prompt: str) -> str:
    """
    Fast image generation using NanoBanana (Gemini 2.5 Flash Image).
    
    Use for: Quick drafts, A/B testing, concept exploration.
    - ~3-10 seconds
    - Up to 2K resolution
    - ~$0.06 per image
    """
    from google import genai
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash-image",  # NanoBanana (faster, cheaper)
        contents=[prompt]
    )
    
    for part in response.parts:
        if part.inline_data is not None:
            return save_image(part.as_image())

@tool
def create_figma_design(design_spec: dict) -> dict:
    """
    Create design in Figma via API.
    
    Args:
        design_spec: {
            "type": "landing_page",
            "sections": ["hero", "features", "pricing", "cta"],
            "style": "modern_saas",
            "colors": {"primary": "#3B82F6"}
        }
    
    Returns:
        {"figma_url": "...", "preview_png": "..."}
    """
    # Uses Figma Plugin API
    pass

@tool
def analyze_design_reference(url: str) -> dict:
    """
    Analyze reference design for inspiration.
    
    Returns:
        {
            "color_palette": [...],
            "fonts_detected": [...],
            "layout_pattern": "hero_features_testimonials_cta",
            "style": "minimalist|playful|corporate"
        }
    """
    pass

@tool
def generate_color_palette(base_color: str, mood: str) -> dict:
    """
    Generate harmonious color palette.
    
    Args:
        base_color: Hex color like "#3B82F6"
        mood: 'professional', 'playful', 'elegant', 'energetic'
    
    Returns:
        {
            "primary": "#3B82F6",
            "secondary": "#1E293B",
            "accent": "#22C55E",
            "background": "#F8FAFC",
            "text": "#0F172A"
        }
    """
    pass

@tool
def generate_icons(names: list, style: str) -> dict:
    """
    Generate icon set.
    
    Args:
        names: ["dashboard", "settings", "user", "notification"]
        style: 'outline', 'solid', 'duotone'
    
    Returns:
        {"icons": [{"name": "dashboard", "svg": "..."}]}
    """
    pass

DESIGN_AGENT_TOOLS = [
    generate_image,
    create_figma_design,
    analyze_design_reference,
    generate_color_palette,
    generate_icons
]
```

### Task Types

| Task Type | Input | Output |
|-----------|-------|--------|
| `landing_page` | Copy, brand colors | Desktop + Mobile mockups |
| `dashboard` | Features list, data types | UI mockup with components |
| `logo` | Company name, industry, style | Logo variations (PNG, SVG) |
| `icon_set` | Icon names, style | SVG icons |
| `social_graphics` | Content, platform | Sized graphics for each platform |
| `email_template` | Email copy | HTML email design |

### Design System Integration

```python
class DesignSystemSpec:
    """Standard design system for consistent outputs."""
    
    DEFAULT_TOKENS = {
        "colors": {
            "primary": "#3B82F6",     # Blue
            "secondary": "#64748B",   # Slate
            "success": "#22C55E",     # Green
            "warning": "#F59E0B",     # Amber
            "error": "#EF4444",       # Red
            "background": "#FFFFFF",
            "surface": "#F8FAFC",
            "text": "#0F172A",
            "text_secondary": "#64748B"
        },
        "typography": {
            "font_family": "Inter, system-ui, sans-serif",
            "heading_sizes": ["48px", "36px", "24px", "20px", "16px"],
            "body_size": "16px",
            "line_height": 1.5
        },
        "spacing": {
            "unit": 4,  # 4px base
            "scale": [0, 4, 8, 12, 16, 24, 32, 48, 64, 96]
        },
        "radius": {
            "sm": "4px",
            "md": "8px",
            "lg": "12px",
            "full": "9999px"
        }
    }

    @classmethod
    def merge_with_project(cls, project_brand: dict) -> dict:
        """Merge default tokens with project-specific branding."""
        return {**cls.DEFAULT_TOKENS, **project_brand}
```

### Example Workflow

```python
async def design_agent_node(state: AgentState) -> AgentState:
    task = state["current_task"]
    project = state["project"]
    
    # 1. Get design system (project-specific or default)
    design_system = DesignSystemSpec.merge_with_project(
        project.brand_guidelines or {}
    )
    
    # 2. If references provided, analyze them
    if task.reference_urls:
        references = await asyncio.gather(*[
            tools.analyze_design_reference(url) 
            for url in task.reference_urls
        ])
    
    # 3. Generate design based on task type
    if task.design_type == "landing_page":
        # Get content from Content Agent
        content = await get_artifact(state["content_artifact_id"])
        
        design = await tools.create_figma_design({
            "type": "landing_page",
            "content": content,
            "design_system": design_system,
            "style": task.style or "modern_saas"
        })
    
    elif task.design_type == "graphic":
        image = await tools.generate_image(
            prompt=task.description,
            style=task.style or "illustration",
            size=task.size or "1024x1024"
        )
        design = {"image_url": image}
    
    # 4. Store artifact
    artifact = await store_artifact(
        project_id=state["project_id"],
        type="image",
        storage_url=design.get("figma_url") or design.get("image_url"),
        metadata={"design_system": design_system}
    )
    
    return {
        **state,
        "design_artifact_id": artifact.id,
        "next_node": "critic_agent"
    }
```

---

## 🔄 Content + Design Coordination

Many tasks require both agents. Here's how they coordinate:

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Planner   │────▶│   Content   │────▶│   Design    │
│             │     │   Agent     │     │   Agent     │
└─────────────┘     └──────┬──────┘     └──────┬──────┘
                           │                   │
                    writes copy          uses copy in
                           │               mockups
                           ▼                   │
                    ┌─────────────┐            │
                    │  Artifact   │◀───────────┘
                    │  (content)  │
                    └─────────────┘
```

### Parallel vs Sequential

| Scenario | Execution | Why |
|----------|-----------|-----|
| Landing page | Sequential | Design needs copy first |
| Logo + Copy | Parallel | Independent tasks |
| Email template | Sequential | Design wraps around copy |
| Icon set | Design only | No content needed |
| Documentation | Content only | No design needed |

---

## 📊 Quality Metrics

### Content Agent

| Metric | Target | Measurement |
|--------|--------|-------------|
| Readability Score | > 60 Flesch | Automated |
| Grammar Errors | 0 | Grammarly API |
| Plagiarism | < 5% | Copyscape |
| Client Revision Rate | < 20% | Tracked |

### Design Agent

| Metric | Target | Measurement |
|--------|--------|-------------|
| Accessibility | WCAG AA | Automated scan |
| Consistency | 100% tokens | Design system check |
| Client Revision Rate | < 25% | Tracked |
| Generation Time | < 60s for mockups | Logged |
