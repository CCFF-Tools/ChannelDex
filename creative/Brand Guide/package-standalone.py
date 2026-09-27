from pathlib import Path
import base64
import re

root = Path(__file__).parent
dist = root / "dist"
source_path = dist / "index.html"
output_path = root / "ChannelDex Brand Guide v1.2.html"

html = source_path.read_text()

font_files = [
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Medium.ttf",
    "IBMPlexSans-SemiBold.ttf",
    "IBMPlexSans-Bold.ttf",
    "IBMPlexMono-Regular.ttf",
    "IBMPlexMono-SemiBold.ttf",
]

for filename in font_files:
    encoded = base64.b64encode((dist / "fonts" / filename).read_bytes()).decode()
    html = html.replace(
        f"url('fonts/{filename}')",
        f"url('data:font/ttf;base64,{encoded}')",
    )


def embed_asset(match):
    attribute, filename = match.groups()
    asset_path = dist / "assets" / filename
    encoded = base64.b64encode(asset_path.read_bytes()).decode()
    mime = "image/png" if asset_path.suffix.lower() == ".png" else "application/octet-stream"
    return f'{attribute}="data:{mime};base64,{encoded}"'


html = re.sub(r'(src|href)="assets/([^"]+)"', embed_asset, html)
output_path.write_text(html)
print(output_path)
