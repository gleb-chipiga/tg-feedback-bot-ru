__all__ = ("Json",)

type Json = str | int | float | bool | dict[str, "Json"] | list["Json"] | None
