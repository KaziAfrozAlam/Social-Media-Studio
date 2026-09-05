from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConstraintProfile:
    platform: str
    max_length: int
    tone: str
    min_hashtags: int = 0
    max_hashtags: int = 0
    max_participants: int | None = None
    allows_rich_text: bool = True

    def validate(self, content: str) -> list[str]:
        violations: list[str] = []
        length = len(content)
        if length > self.max_length:
            violations.append(
                f"{self.platform}: content length {length} exceeds max {self.max_length}"
            )
        hashtags = count_hashtags(content)
        if hashtags < self.min_hashtags:
            violations.append(
                f"{self.platform}: hashtag count {hashtags} below minimum {self.min_hashtags}"
            )
        if hashtags > self.max_hashtags:
            violations.append(
                f"{self.platform}: hashtag count {hashtags} exceeds maximum {self.max_hashtags}"
            )
        return violations


def count_hashtags(content: str) -> int:
    return len(
        [w for w in content.replace("\n", " ").split() if w.startswith("#") and len(w) > 1]
    )


def get_profile(platform: str) -> ConstraintProfile:
    try:
        return PROFILES[platform]
    except KeyError:
        raise UnknownPlatformError(platform)


class UnknownPlatformError(KeyError):
    def __init__(self, platform: str):
        super().__init__(f"unknown platform: {platform}")


PROFILES: dict[str, ConstraintProfile] = {
    "telegram": ConstraintProfile(
        platform="telegram",
        max_length=4096,
        tone="informative",
        max_hashtags=5,
        allows_rich_text=True,
    ),
    "x": ConstraintProfile(
        platform="x",
        max_length=280,
        tone="punchy",
        max_hashtags=3,
        allows_rich_text=False,
    ),
    "linkedin": ConstraintProfile(
        platform="linkedin",
        max_length=3000,
        tone="professional",
        min_hashtags=3,
        max_hashtags=5,
        allows_rich_text=True,
    ),
    "instagram": ConstraintProfile(
        platform="instagram",
        max_length=2200,
        tone="casual-friendly",
        min_hashtags=5,
        max_hashtags=30,
        allows_rich_text=True,
    ),
}