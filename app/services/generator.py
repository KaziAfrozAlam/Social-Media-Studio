from app.models import Post, Variant, new_id
from app.constraint_profiles.profiles import get_profile


class VariantValidationError(Exception):
    def __init__(self, platform: str, violations: list[str]):
        self.platform = platform
        self.violations = violations
        super().__init__(f"variant for '{platform}' violates constraints: {'; '.join(violations)}")


def generate_variant_for(post: Post, platform: str) -> str:
    profile = get_profile(platform)
    if profile.platform == "x":
        text = render_x(post)
    elif profile.platform == "linkedin":
        text = render_linkedin(post)
    elif profile.platform == "instagram":
        text = render_instagram(post)
    elif profile.platform == "telegram":
        text = render_telegram(post)
    else:
        raise KeyError(platform)

    violations = profile.validate(text)
    if violations:
        raise VariantValidationError(platform, violations)
    return text


def create_variant(post: Post, platform: str, content: str | None = None) -> Variant:
    profile = get_profile(platform)
    text = content if content is not None else generate_variant_for(post, platform)
    violations = profile.validate(text)
    if violations:
        raise VariantValidationError(platform, violations)
    return Variant(
        id=new_id(),
        post_id=post.id,
        platform=platform,
        content=text,
        status="draft",
    )


def _excerpt(post: Post, limit: int = 140) -> str:
    body = (post.content or "").strip().replace("\r\n", " ").replace("\n", " ")
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


def render_x(post: Post) -> str:
    title = post.title or "Our latest update"
    excerpt = _excerpt(post, 200)
    line = f"🚨 {title}\n\n{excerpt}"
    if len(line) > 270:
        line = line[:269].rstrip() + "…"
    line += "\n\n#shorts"
    return line


def render_linkedin(post: Post) -> str:
    title = post.title or "Our latest update"
    excerpt = _excerpt(post, 300)
    return (
        f"📌 {title}\n\n"
        f"{excerpt}\n\n"
        f"We just published a deep dive and we think it's worth your time. "
        f"Read the full post and let us know what you think.\n\n"
        f"#technology #innovation #insights"
    )


def render_instagram(post: Post) -> str:
    title = post.title or "Our latest update"
    excerpt = _excerpt(post, 180)
    tags = "#socialmedia #marketing #content #growth #digital #branding #storytelling #creativity #community #ideas #design #future #tech #learning #inspiration #news #writing #business #trending #daily"
    return (
        f"✨ {title}\n\n"
        f"{excerpt}\n\n"
        f"Save this for later — you'll thank us. 💾\n\n"
        f"{tags}"
    )


def render_telegram(post: Post) -> str:
    title = post.title or "Our latest update"
    excerpt = _excerpt(post, 500)
    return (
        f"📰 {title}\n\n"
        f"{excerpt}\n\n"
        f"Full story on our blog. Stay tuned for more."
    )