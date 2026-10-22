#!/usr/bin/env python3
"""
HT-GEN // CMYK Halftone, Comic Screen-Print & Dither CLI Processor
Companion CLI to the tone/2 web app (index.html in this folder).

Usage examples:
  # 1. Classic Photoshop Color Halftone (108/162/90/45 rosette) + SVG vector export
  python3 tone2.py input.jpg -o output-halftone.png --svg output-vectors.svg

  # 2. 70s Pulp Comic (Ben-Day dots + posterize + black comic ink outlines + misregistration)
  python3 tone2.py input.jpg -o comic.png --preset comic_70s

  # 3. Custom colors, dot radius, edge bleed & separated C/M/Y/K screen-print film plates
  python3 tone2.py input.jpg -o print.png --dot-size 12 --radius-scale 1.25 \
    --edge-roughness 0.3 --ink-melt --plates-dir ./plates
"""

import argparse
import math
import os
import sys
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


PRESETS = {
    "ps_rosette": {
        "mode": "halftone",
        "dot_size": 10,
        "radius_scale": 1.1,
        "edge_hardness": 0.92,
        "edge_roughness": 0.1,
        "shape": "circle",
        "gcr": 0.75,
        "angles": [108.0, 162.0, 90.0, 45.0],
        "colors": ["#00AEEF", "#EC008C", "#FFF200", "#141413"],
        "paper": "#FFFFFF",
        "misreg": 0.0,
        "comic_edge": False,
        "posterize": 0,
        "ink_melt": False,
    },
    "comic_70s": {
        "mode": "halftone",
        "dot_size": 9,
        "radius_scale": 1.15,
        "edge_hardness": 0.85,
        "edge_roughness": 0.22,
        "shape": "circle",
        "gcr": 0.85,
        "angles": [15.0, 75.0, 0.0, 45.0],
        "colors": ["#0095D9", "#E6007E", "#FFED00", "#181512"],
        "paper": "#F4EAD2",
        "misreg": 1.5,
        "comic_edge": True,
        "edge_threshold": 32,
        "edge_weight": 1.75,
        "posterize": 5,
        "ink_melt": False,
    },
    "screenprint_poster": {
        "mode": "halftone",
        "dot_size": 12,
        "radius_scale": 1.25,
        "edge_hardness": 0.95,
        "edge_roughness": 0.35,
        "shape": "circle",
        "gcr": 0.8,
        "angles": [22.5, 52.5, 7.5, 82.5],
        "colors": ["#008094", "#D92B2B", "#F2B705", "#141414"],
        "paper": "#EFE6D5",
        "misreg": 2.0,
        "comic_edge": True,
        "edge_threshold": 28,
        "edge_weight": 2.0,
        "posterize": 4,
        "ink_melt": False,
    },
    "risograph": {
        "mode": "halftone",
        "dot_size": 8,
        "radius_scale": 1.15,
        "edge_hardness": 0.75,
        "edge_roughness": 0.3,
        "shape": "circle",
        "gcr": 0.4,
        "angles": [15.0, 75.0, 0.0, 45.0],
        "colors": ["#0078BF", "#FF48B0", "#FFE800", "#1E1E1E"],
        "paper": "#F7F3EB",
        "misreg": 2.5,
        "comic_edge": False,
        "posterize": 0,
        "ink_melt": False,
    },
    "ink_bleed_melt": {
        "mode": "halftone",
        "dot_size": 11,
        "radius_scale": 1.25,
        "edge_hardness": 0.8,
        "edge_roughness": 0.4,
        "shape": "circle",
        "gcr": 0.8,
        "angles": [108.0, 162.0, 90.0, 45.0],
        "colors": ["#00AEEF", "#EC008C", "#FFF200", "#141210"],
        "paper": "#F2E9D8",
        "misreg": 1.0,
        "comic_edge": True,
        "edge_threshold": 35,
        "edge_weight": 1.5,
        "posterize": 0,
        "ink_melt": True,
    },
}


def mulberry32(seed: int):
    state = seed & 0xFFFFFFFF

    def rand():
        nonlocal state
        state = (state + 0x6D2B79F5) & 0xFFFFFFFF
        t = ((state ^ (state >> 15)) * (1 | state)) & 0xFFFFFFFF
        t = (t + (((t ^ (t >> 7)) * (61 | t)) & 0xFFFFFFFF)) ^ t
        t &= 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    return rand


def hex_to_rgb(hex_str: str):
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_cmyk(r: int, g: int, b: int, gcr: float):
    c = 1.0 - (r / 255.0)
    m = 1.0 - (g / 255.0)
    y = 1.0 - (b / 255.0)
    min_cmy = min(c, m, y)
    k = min_cmy * gcr
    if k >= 0.999:
        return (0.0, 0.0, 0.0, 1.0)
    denom = 1.0 - k
    return (
        max(0.0, (c - k) / denom),
        max(0.0, (m - k) / denom),
        max(0.0, (y - k) / denom),
        min_cmy,
    )


def draw_dot_polygon(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, shape: str, angle_rad: float, rough_amt: float, rand):
    if rough_amt > 0.02 and shape in ("circle", "ellipse"):
        lobes = 10
        rx = r * 1.3 if shape == "ellipse" else r
        ry = r * 0.75 if shape == "ellipse" else r
        c, s = math.cos(angle_rad), math.sin(angle_rad)
        pts = []
        for i in range(lobes):
            theta = (i / lobes) * math.pi * 2.0
            wobble = 1.0 + (rand() * 2.0 - 1.0) * (rough_amt * 0.45)
            lx = math.cos(theta) * rx * wobble
            ly = math.sin(theta) * ry * wobble
            pts.append((x + lx * c - ly * s, y + lx * s + ly * c))
        draw.polygon(pts, fill=255)
        return

    if shape == "square":
        c, s = math.cos(angle_rad), math.sin(angle_rad)
        corners = [(r, r), (-r, r), (-r, -r), (r, -r)]
        pts = [(x + dx * c - dy * s, y + dx * s + dy * c) for dx, dy in corners]
        draw.polygon(pts, fill=255)
    elif shape == "diamond":
        dr = r * 1.15
        draw.polygon([(x, y - dr), (x + dr, y), (x, y + dr), (x - dr, y)], fill=255)
    elif shape == "hex":
        pts = [
            (x + math.cos(angle_rad + i * math.pi / 3.0) * r, y + math.sin(angle_rad + i * math.pi / 3.0) * r)
            for i in range(6)
        ]
        draw.polygon(pts, fill=255)
    elif shape == "line":
        c, s = math.cos(angle_rad), math.sin(angle_rad)
        hw = max(0.75, r * 0.42)
        ln = r * 1.25
        dx, dy = c * ln, s * ln
        nx, ny = -s * hw, c * hw
        draw.polygon(
            [(x - dx + nx, y - dy + ny), (x + dx + nx, y + dy + ny), (x + dx - nx, y + dy - ny), (x - dx - nx, y - dy - ny)],
            fill=255,
        )
    else:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=255)


def render_comic_keyline_mask(img_rgb: Image.Image, threshold: float, weight: float) -> Image.Image:
    gray = img_rgb.convert("L")
    w, h = gray.size
    px = gray.tobytes()
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    thresh_val = threshold * 3.2
    rad = max(0.5, weight * 0.5)

    for y in range(1, h - 1):
        row_up = (y - 1) * w
        row_mid = y * w
        row_dn = (y + 1) * w
        for x in range(1, w - 1):
            gx = (
                -px[row_up + x - 1] + px[row_up + x + 1]
                - 2 * px[row_mid + x - 1] + 2 * px[row_mid + x + 1]
                - px[row_dn + x - 1] + px[row_dn + x + 1]
            )
            gy = (
                -px[row_up + x - 1] - 2 * px[row_up + x] - px[row_up + x + 1]
                + px[row_dn + x - 1] + 2 * px[row_dn + x] + px[row_dn + x + 1]
            )
            mag = math.hypot(gx, gy)
            if mag > thresh_val:
                if rad <= 0.8:
                    mask.putpixel((x, y), 255)
                else:
                    draw.ellipse((x - rad, y - rad, x + rad, y + rad), fill=255)
    return mask


def apply_bg_removal(src: Image.Image, mode: str, key_hex: str, tolerance: float) -> Image.Image:
    if not mode or mode == "none":
        return src
    w, h = src.size
    rgba = bytearray(src.tobytes())
    kr, kg, kb = hex_to_rgb(key_hex or "#FFFFFF")
    tol = tolerance * 2.6
    feather = 25.0

    def dist_at(idx: int) -> float:
        dr = rgba[idx] - kr
        dg = rgba[idx + 1] - kg
        db = rgba[idx + 2] - kb
        return math.sqrt(dr * dr + dg * dg + db * db)

    if mode == "smart_flood":
        visited = bytearray(w * h)
        queue = []
        for x in range(w):
            queue.append(x)
            queue.append((h - 1) * w + x)
            visited[x] = 1
            visited[(h - 1) * w + x] = 1
        for y in range(1, h - 1):
            queue.append(y * w)
            queue.append(y * w + (w - 1))
            visited[y * w] = 1
            visited[y * w + (w - 1)] = 1

        head = 0
        while head < len(queue):
            p = queue[head]
            head += 1
            idx = p * 4
            d = dist_at(idx)
            if d <= tol:
                rgba[idx + 3] = 0
            elif d <= tol + feather:
                ramp = (d - tol) / feather
                rgba[idx + 3] = min(rgba[idx + 3], int(ramp * 255))
            if d <= tol * 1.12:
                x = p % w
                y = p // w
                for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                    if 0 <= nx < w and 0 <= ny < h:
                        np_idx = ny * w + nx
                        if not visited[np_idx]:
                            visited[np_idx] = 1
                            queue.append(np_idx)
    elif mode == "color_key":
        for i in range(0, len(rgba), 4):
            d = dist_at(i)
            if d <= tol:
                rgba[i + 3] = 0
            elif d <= tol + feather:
                rgba[i + 3] = min(rgba[i + 3], int(((d - tol) / feather) * 255))
    elif mode == "luma_white":
        cutoff = 255.0 - tolerance * 2.1
        for i in range(0, len(rgba), 4):
            lum = 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]
            if lum >= cutoff:
                rgba[i + 3] = 0
    elif mode == "luma_black":
        cutoff = tolerance * 2.1
        for i in range(0, len(rgba), 4):
            lum = 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2]
            if lum <= cutoff:
                rgba[i + 3] = 0

    return Image.frombytes("RGBA", (w, h), bytes(rgba))


def process_image(args):
    src = Image.open(args.input).convert("RGBA")
    if args.max_size and max(src.size) > args.max_size:
        ratio = args.max_size / float(max(src.size))
        src = src.resize((int(src.width * ratio), int(src.height * ratio)), Image.Resampling.LANCZOS)

    if getattr(args, "remove_bg", "none") != "none":
        src = apply_bg_removal(src, args.remove_bg, args.bg_key, args.bg_tolerance)

    w, h = src.size
    alpha_channel = src.split()[3]
    alpha_bytes = alpha_channel.tobytes()
    has_native_alpha = min(alpha_bytes) < 245

    # Composite transparent pixels onto white before RGB extraction so R=0,G=0,B=0,A=0 never acts as black
    white_bg = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    rgb_work = Image.alpha_composite(white_bg, src).convert("RGB")

    if args.posterize and args.posterize > 1:
        bits = max(1, min(8, int(math.ceil(math.log2(args.posterize)))))
        rgb_work = ImageOps.posterize(rgb_work, bits)

    rgb_bytes = rgb_work.tobytes()

    angles = [float(a) for a in args.angles.split(",")]
    colors = [c.strip() for c in args.colors.split(",")]
    ch_names = ["c", "m", "y", "k"]
    misreg_dirs = [(-1.0, -0.5), (1.0, -0.5), (-0.5, 1.0), (0.0, 0.0)]

    # Start with white for subtractive multiply, then restore alpha mask at the end!
    pr, pg, pb = (255, 255, 255) if args.paper.lower() == "transparent" else hex_to_rgb(args.paper)
    composite_rgb = Image.new("RGB", (w, h), (pr, pg, pb))

    cx, cy = w / 2.0, h / 2.0
    diag = math.hypot(w, h)
    half_diag = diag / 2.0
    dot_size = max(2, args.dot_size)
    base_max_r = (dot_size * args.max_radius_scale) / 2.0

    vector_layers = []
    plate_masks = {}

    for idx, ch_id in enumerate(ch_names):
        angle_deg = angles[idx % len(angles)]
        angle_rad = math.radians(angle_deg)
        cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
        ink_hex = colors[idx % len(colors)]
        ink_rgb = hex_to_rgb(ink_hex)
        dx = misreg_dirs[idx][0] * args.misreg
        dy = misreg_dirs[idx][1] * args.misreg

        rand = mulberry32(args.seed * 7919 + int(angle_deg * 31) + ord(ch_id))
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        dots_vec = []

        y = -half_diag
        while y < half_diag:
            x = -half_diag
            while x < half_diag:
                gx = x + ((rand() - 0.5) * dot_size * args.jitter if args.jitter > 0 else 0.0)
                gy = y + ((rand() - 0.5) * dot_size * args.jitter if args.jitter > 0 else 0.0)
                sx = cx + (gx * cos_a - gy * sin_a)
                sy = cy + (gx * sin_a + gy * cos_a)

                if -dot_size <= sx < w + dot_size and -dot_size <= sy < h + dot_size:
                    ix = min(max(int(sx), 0), w - 1)
                    iy = min(max(int(sy), 0), h - 1)
                    p_idx = iy * w + ix
                    if alpha_bytes[p_idx] >= 12:
                        rgb_idx = p_idx * 3
                        r_val = rgb_bytes[rgb_idx]
                        g_val = rgb_bytes[rgb_idx + 1]
                        b_val = rgb_bytes[rgb_idx + 2]
                        cmyk = rgb_to_cmyk(r_val, g_val, b_val, args.gcr)
                        val = cmyk[idx]
                        if val > args.min_radius:
                            radius = base_max_r * math.sqrt(min(1.5, val)) * args.radius_scale
                            if args.noise > 0:
                                radius *= 1.0 + (rand() * 2.0 - 1.0) * args.noise
                            if radius > 0.35:
                                draw_x = sx + dx
                                draw_y = sy + dy
                                draw_dot_polygon(
                                    draw, draw_x, draw_y, radius, args.shape, angle_rad, args.edge_roughness, rand
                                )
                                dots_vec.append((draw_x, draw_y, radius, angle_deg))
                x += dot_size
            y += dot_size

        if args.edge_hardness < 0.95:
            blur_r = (1.0 - args.edge_hardness) * (dot_size * 0.4)
            if blur_r > 0.2:
                mask = mask.filter(ImageFilter.GaussianBlur(radius=blur_r))

        if args.ink_melt:
            mask = mask.filter(ImageFilter.GaussianBlur(radius=1.3))
            lut = [255 if p > 110 else 0 for p in range(256)]
            mask = mask.point(lut)

        plate_masks[ch_id] = mask
        vector_layers.append((ch_id, ink_hex, dots_vec))

        # Subtractive Multiply blend onto composite_rgb
        plate_rgb = Image.new("RGB", (w, h), (255, 255, 255))
        ink_layer = Image.new("RGB", (w, h), ink_rgb)
        plate_rgb.paste(ink_layer, mask=mask)

        composite_rgb = ImageChops.multiply(composite_rgb, plate_rgb)

    composite = composite_rgb.convert("RGBA")

    if args.comic_edge:
        edge_mask = render_comic_keyline_mask(rgb_work, args.edge_threshold, args.edge_weight)
        edge_rgb = hex_to_rgb(args.edge_color)
        ink_edge_img = Image.new("RGBA", (w, h), (*edge_rgb, 255))
        composite.paste(ink_edge_img, mask=edge_mask)

    # Preserve PNG alpha cutout or apply full ink-only transparency
    if args.transparent or args.paper.lower() == "transparent":
        # Union of all ink plate masks clipped to source alpha
        combined_ink = Image.new("L", (w, h), 0)
        for m in plate_masks.values():
            combined_ink = ImageChops.lighter(combined_ink, m)
        final_alpha = ImageChops.darker(combined_ink, alpha_channel) if has_native_alpha or args.remove_bg != "none" else combined_ink
        composite.putalpha(final_alpha)
    elif has_native_alpha or args.remove_bg != "none":
        composite.putalpha(alpha_channel)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    composite.save(args.output)
    print(f"[TONE/2] Saved halftone render -> {args.output} ({w}x{h}px)")

    if args.plates_dir:
        os.makedirs(args.plates_dir, exist_ok=True)
        for ch_id, mask in plate_masks.items():
            # Save black-on-white / transparent film positive for silkscreen burning
            film = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
            film.paste(black, mask=mask)
            plate_path = os.path.join(args.plates_dir, f"plate-{ch_id.upper()}-film.png")
            film.save(plate_path)
            print(f"[HT-GEN] Saved separated plate -> {plate_path}")

    if args.svg:
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">']
        if args.paper.lower() != "transparent":
            lines.append(f'  <rect id="Paper" width="{w}" height="{h}" fill="{args.paper}"/>')
        for ch_id, ink_hex, dots in vector_layers:
            lines.append(f'  <g id="Plate_{ch_id.upper()}" fill="{ink_hex}" style="mix-blend-mode:multiply;">')
            for dx_pos, dy_pos, rad, ang in dots:
                if args.shape == "square":
                    side = rad * 2.0
                    lines.append(
                        f'    <rect x="{dx_pos - rad:.2f}" y="{dy_pos - rad:.2f}" width="{side:.2f}" height="{side:.2f}" transform="rotate({ang:.1f} {dx_pos:.2f} {dy_pos:.2f})"/>'
                    )
                elif args.shape == "diamond":
                    dr = rad * 1.15
                    lines.append(
                        f'    <polygon points="{dx_pos:.2f},{dy_pos - dr:.2f} {dx_pos + dr:.2f},{dy_pos:.2f} {dx_pos:.2f},{dy_pos + dr:.2f} {dx_pos - dr:.2f},{dy_pos:.2f}"/>'
                    )
                else:
                    lines.append(f'    <circle cx="{dx_pos:.2f}" cy="{dy_pos:.2f}" r="{rad:.2f}"/>')
            lines.append("  </g>")
        lines.append("</svg>")
        with open(args.svg, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"[HT-GEN] Saved vector SVG -> {args.svg}")


def main():
    parser = argparse.ArgumentParser(description="HT-GEN // CMYK Halftone & Comic Screen-Print Processor")
    parser.add_argument("input", help="Path to source image")
    parser.add_argument("-o", "--output", default="output-halftone.png", help="Output PNG path")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), help="Apply a built-in look preset")
    parser.add_argument("--mode", default="halftone_rip", choices=["halftone_rip", "halftone", "modulation", "dither_cmyk", "dither_tonal", "dither_indexed", "stipple"])
    parser.add_argument("--rip-spot", default="euclidean", choices=["euclidean", "cosine", "ellipse", "diamond", "line", "cross", "rings", "mezzotint"])
    parser.add_argument("--dot-size", type=int, default=9, help="Halftone cell pitch in pixels (default: 9)")
    parser.add_argument("--radius-scale", type=float, default=1.05, help="Dot radius / gain multiplier (default: 1.05)")
    parser.add_argument("--min-radius", type=float, default=0.04, help="Highlight cutoff threshold (0..0.4)")
    parser.add_argument("--max-radius-scale", type=float, default=1.35, help="Max dot radius ratio (default: 1.35)")
    parser.add_argument("--shape", default="circle", choices=["circle", "ellipse", "square", "diamond", "hex", "cross", "ring", "line", "star"])
    parser.add_argument("--edge-hardness", type=float, default=0.92, help="Dot edge hardness 0.1..1.0 (default: 0.92)")
    parser.add_argument("--edge-roughness", type=float, default=0.18, help="Organic silkscreen dot bleed roughness 0..0.85")
    parser.add_argument("--jitter", type=float, default=0.04, help="Grid jitter 0..1")
    parser.add_argument("--noise", type=float, default=0.06, help="Dot size noise 0..1")
    parser.add_argument("--seed", type=int, default=1337, help="Deterministic PRNG seed")
    parser.add_argument("--gcr", type=float, default=0.75, help="Black generation / GCR pull 0..1 (default: 0.75)")
    parser.add_argument("--angles", default="15,75,0,45", help="C,M,Y,K screen angles in degrees")
    parser.add_argument("--colors", default="#00AEEF,#EC008C,#FFF200,#141413", help="C,M,Y,K hex ink colors")
    parser.add_argument("--paper", default="#FFFFFF", help="Paper hex color or 'transparent'")
    parser.add_argument("--transparent", action="store_true", help="Output ink dots on transparent background")
    parser.add_argument("--remove-bg", default="none", choices=["none", "smart_flood", "color_key", "luma_white", "luma_black"], help="Background removal mode")
    parser.add_argument("--bg-key", default="#FFFFFF", help="Background key hex color to remove")
    parser.add_argument("--bg-tolerance", type=float, default=28.0, help="Background removal tolerance 1..100")
    parser.add_argument("--misreg", type=float, default=0.0, help="Plate misregistration offset in pixels")
    parser.add_argument("--underprint", type=float, default=0.0, help="Comic flat-color underprint wash opacity 0..1")
    parser.add_argument("--comic-edge", action="store_true", help="Enable Sobel comic ink outline pass")
    parser.add_argument("--comic-style", default="contour", choices=["contour", "noir", "stamp"], help="Comic inking style")
    parser.add_argument("--edge-threshold", type=float, default=35.0, help="Comic outline edge sensitivity")
    parser.add_argument("--edge-weight", type=float, default=1.5, help="Comic outline stroke thickness")
    parser.add_argument("--edge-color", default="#111111", help="Comic outline ink color")
    parser.add_argument("--posterize", type=int, default=0, help="Quantize color levels before halftoning (e.g. 5)")
    parser.add_argument("--ink-melt", action="store_true", help="Enable PS Live Ink Bleed (blur + threshold melt)")
    parser.add_argument("--proof-border", action="store_true", help="Add screen-print registration marks and proof border")
    parser.add_argument("--dither-algo", default="atkinson", help="Dither algorithm for dither modes")
    parser.add_argument("--pixel-scale", type=int, default=2, help="Pixel scale for dither modes")
    parser.add_argument("--max-size", type=int, default=1400, help="Max dimension in pixels")
    parser.add_argument("--svg", help="Optional path to export scalable Illustrator SVG")
    parser.add_argument("--plates-dir", help="Optional directory to export 4 separated C/M/Y/K film plates")

    args = parser.parse_args()

    if args.preset and args.preset in PRESETS:
        p = PRESETS[args.preset]
        for k, v in p.items():
            if k == "angles":
                args.angles = ",".join(str(x) for x in v)
            elif k == "colors":
                args.colors = ",".join(v)
            else:
                setattr(args, k, v)

    process_image(args)


if __name__ == "__main__":
    main()
