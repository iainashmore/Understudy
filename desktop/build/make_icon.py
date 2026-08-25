"""The icon: a stand-in, waiting outside the spotlight.

One figure in the light, an identical one behind it in the dark. That is what
an understudy is, and it is the idea the application is named for.

The pool of light is doing real work. Two figures on their own is the generic
"contacts" icon every address book uses; the light is what makes these two a
principal and a stand-in rather than two colleagues. A soft-edged circle rather
than a beam, because a beam is a gradient and a gradient at 16px is a grey
smear, whereas a circle is still a circle.

Drawn in code rather than an editor because the size that decides whether an
icon works is 16px in a taskbar, and iterating on that is faster in a script.
"""

from PIL import Image, ImageChops, ImageDraw, ImageFilter

SS = 4                                  # supersample, then resize down

PLATE  = (22, 25, 30)                   # near the application's own background
LIGHT  = (58, 78, 100)                  # the pool of light on the plate
FRONT  = (116, 190, 245)                # the principal, lit
BEHIND = (66, 84, 102)                  # the understudy, in the dark


def figure(draw, cx, top, scale, colour):
    """Head and shoulders. Nothing clever -- a circle and a rounded arch."""
    head_r = 0.150 * scale
    head_cy = top + head_r
    draw.ellipse((cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
                 fill=colour)

    body_w = 0.480 * scale
    body_top = head_cy + head_r * 1.34
    body_h = 0.360 * scale
    draw.rounded_rectangle(
        (cx - body_w / 2, body_top, cx + body_w / 2, body_top + body_h),
        radius=body_w * 0.42, fill=colour,
    )
    draw.rectangle((cx - body_w / 2, body_top + body_h * 0.55,
                    cx + body_w / 2, body_top + body_h), fill=colour)


def compose(size):
    W = size * SS
    plate_mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(plate_mask).rounded_rectangle(
        (0, 0, W - 1, W - 1), radius=int(W * 0.22), fill=255)

    scene = Image.new("RGB", (W, W), PLATE)
    draw = ImageDraw.Draw(scene)

    # The beam, thrown from off the top-left corner and widening as it falls.
    # A cone rather than a pool, because a cone says where the light is coming
    # from and that is what puts one figure on stage and the other in the wings.
    beam = Image.new("L", (W, W), 0)
    ImageDraw.Draw(beam).polygon(
        [(-W * 0.12, -W * 0.12), (W * 0.26, -W * 0.12),
         (W * 0.86, W * 1.12), (-W * 0.12, W * 1.12)],
        fill=255,
    )
    # Barely softened. A heavily blurred beam turns into a grey smear the
    # moment it is scaled down, and a smear reads as a rendering fault.
    beam = beam.filter(ImageFilter.GaussianBlur(W * 0.012))
    scene.paste(Image.new("RGB", (W, W), LIGHT), (0, 0), beam)

    draw = ImageDraw.Draw(scene)

    # The understudy: behind, smaller, stepped left, out of the light.
    figure(draw, cx=W * 0.660, top=W * 0.310, scale=W * 0.78, colour=BEHIND)

    # A gap punched around the principal so the two never merge into one
    # silhouette. This is the difference between "two people" and "a blob" at
    # 16px, and the only reason the composition survives down there. It is cut
    # from the plate colour, so it reads as a dark rim rather than a glow.
    gap = W * 0.042
    cut = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    figure(ImageDraw.Draw(cut), cx=W * 0.400, top=W * 0.230 - gap,
           scale=W * 0.94 + gap * 2, colour=(0, 0, 0))
    scene.paste(Image.new("RGB", (W, W), PLATE), (0, 0), cut.getchannel("A"))

    draw = ImageDraw.Draw(scene)
    figure(draw, cx=W * 0.400, top=W * 0.230, scale=W * 0.94, colour=FRONT)

    out = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    out.paste(scene, (0, 0), plate_mask)
    return out.resize((size, size), Image.LANCZOS)


def main() -> None:
    compose(1024).save("desktop/build/icon.png")
    frames = [compose(s) for s in (16, 24, 32, 48, 64, 128, 256)]
    frames[0].save("desktop/build/icon.ico", format="ICO",
                   sizes=[(f.width, f.height) for f in frames],
                   append_images=frames[1:])
    print(f"icon.png 1024px, icon.ico with {len(frames)} sizes")

    for background, name in (((238, 238, 240), "light"), ((32, 34, 38), "dark")):
        sheet = Image.new("RGB", (860, 300), background)
        x = 40
        for frame in frames:
            sheet.paste(frame.convert("RGB"), (x, 150 - frame.width // 2), frame)
            x += frame.width + 26
        sheet.save("/tmp/claude-0/-home-user-harness/"
                   f"15c7ee68-d8b4-55ee-a561-e28f9ec3ed31/scratchpad/icon-{name}.png")


if __name__ == "__main__":
    main()
