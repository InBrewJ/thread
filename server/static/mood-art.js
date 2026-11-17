(function (root) {
  "use strict";

  var CANVAS_SIZE = 400;

  var PALETTES = {
    stormy: {
      name: "stormy",
      colors: ["#141b33", "#23304f", "#3b4c6b", "#5c6d8a", "#94a3b8"],
      shadow: "#0b1021",
      accent: "#7c8db5",
    },
    earthy: {
      name: "earthy",
      colors: ["#4a3b2c", "#7c5c34", "#b8863b", "#6f8266", "#c9ba93"],
      shadow: "#2b2118",
      accent: "#d9a441",
    },
    luminous: {
      name: "luminous",
      colors: ["#173b82", "#2563eb", "#d99b26", "#f5c542", "#fef08a"],
      shadow: "#0f2557",
      accent: "#fbbf24",
    },
  };

  var ROBOT_KEYWORDS = [
    "robot",
    "robotics",
    "android",
    "automaton",
    "cyborg",
    "mech",
    "machine",
    "ai ",
    "bot",
    "rocket",
  ];

  var ANIMAL_KEYWORDS = [
    "animal",
    "dog",
    "cat",
    "bird",
    "wolf",
    "fox",
    "bear",
    "horse",
    "owl",
    "fish",
    "pet",
    "wildlife",
    "deer",
  ];

  var HUMANOID_KEYWORDS = [
    "human",
    "humanoid",
    "person",
    "people",
    "friend",
    "family",
    "child",
    "kid",
    "birth",
    "school",
    "graduat",
    "partner",
    "crowd",
    "walk",
    "team",
    "alone",
    "portrait",
  ];

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function toFiniteNumber(value, fallback) {
    if (typeof value === "number" && isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      var parsed = Number(value);
      if (isFinite(parsed)) {
        return parsed;
      }
    }
    return fallback;
  }

  function normalizeEvents(events) {
    if (typeof events === "string") {
      var trimmed = events.trim();
      return trimmed ? [trimmed] : [];
    }
    if (!Array.isArray(events)) {
      return [];
    }
    var out = [];
    for (var i = 0; i < events.length; i++) {
      if (events[i] !== null && events[i] !== undefined) {
        var s = String(events[i]).trim();
        if (s) {
          out.push(s);
        }
      }
    }
    return out;
  }

  function fnv1a32(str) {
    var hash = 0x811c9dc5;
    for (var i = 0; i < str.length; i++) {
      hash ^= str.charCodeAt(i);
      hash = Math.imul(hash, 0x01000193);
    }
    return hash >>> 0;
  }

  /**
   * Deterministic 32-bit PRNG returning values in [0, 1).
   */
  function mulberry32(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) | 0;
      var t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hexToRgb(hex) {
    var clean = hex.replace("#", "");
    var num = parseInt(clean, 16);
    return {
      r: (num >> 16) & 255,
      g: (num >> 8) & 255,
      b: num & 255,
    };
  }

  function rgba(rgb, alpha, delta) {
    var d = delta || 0;
    var r = clamp(Math.round(rgb.r + d), 0, 255);
    var g = clamp(Math.round(rgb.g + d), 0, 255);
    var b = clamp(Math.round(rgb.b + d), 0, 255);
    return "rgba(" + r + "," + g + "," + b + "," + clamp(alpha, 0, 1).toFixed(3) + ")";
  }

  function buildPalette(mood) {
    var base = mood < 4 ? PALETTES.stormy : mood < 7 ? PALETTES.earthy : PALETTES.luminous;
    return {
      name: base.name,
      colors: base.colors.map(hexToRgb),
      shadow: hexToRgb(base.shadow),
      accent: hexToRgb(base.accent),
    };
  }

  function buildSeed(slide, mood, age, eventsList, notes) {
    var idPart = slide && slide.id !== undefined && slide.id !== null ? String(slide.id) : "";
    var seedText = [
      idPart,
      mood.toFixed(3),
      age.toFixed(3),
      eventsList.join("|"),
      notes,
    ].join("::");
    return fnv1a32(seedText);
  }

  function containsAny(haystack, keywords) {
    for (var i = 0; i < keywords.length; i++) {
      if (haystack.indexOf(keywords[i]) !== -1) {
        return true;
      }
    }
    return false;
  }

  function chooseMotif(eventsList, notes, emoji, rng) {
    var combined = (eventsList.join(" ") + " " + notes).toLowerCase();
    var emojiStr = typeof emoji === "string" ? emoji : "";
    var matches = [];

    if (
      containsAny(combined, ROBOT_KEYWORDS) ||
      emojiStr.indexOf("🤖") !== -1 ||
      emojiStr.indexOf("🚀") !== -1
    ) {
      matches.push("robot");
    }
    if (
      containsAny(combined, ANIMAL_KEYWORDS) ||
      /[🐶🐱🐺🦊🐦🦉🐾]/.test(emojiStr)
    ) {
      matches.push("animal");
    }
    if (
      containsAny(combined, HUMANOID_KEYWORDS) ||
      /[👶🎒🎓💼🧑👨👩🤝]/.test(emojiStr)
    ) {
      matches.push("humanoid");
    }

    var primary;
    if (matches.length > 0) {
      primary = matches[Math.floor(rng() * matches.length)];
    } else {
      var fallbackMotifs = ["humanoid", "animal", "robot"];
      primary = fallbackMotifs[Math.floor(rng() * fallbackMotifs.length)];
    }

    var animalSubtype = "wolf";
    if (combined.indexOf("bird") !== -1 || combined.indexOf("owl") !== -1 || /[🐦🦉]/.test(emojiStr)) {
      animalSubtype = "bird";
    } else if (combined.indexOf("cat") !== -1 || emojiStr.indexOf("🐱") !== -1) {
      animalSubtype = "cat";
    } else if (rng() > 0.55) {
      animalSubtype = "bird";
    }

    return { type: primary, animalSubtype: animalSubtype };
  }

  function drawFacetPolygon(ctx, points, fillStyle, strokeStyle) {
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (var i = 1; i < points.length; i++) {
      ctx.lineTo(points[i].x, points[i].y);
    }
    ctx.closePath();
    ctx.fillStyle = fillStyle;
    ctx.fill();
    if (strokeStyle) {
      ctx.strokeStyle = strokeStyle;
      ctx.lineWidth = 1;
      ctx.stroke();
    }
  }

  /**
   * Step 3: Cezanne structural geometric color-faceted background blocks.
   */
  function drawCezanneFacets(ctx, size, palette, rng) {
    ctx.fillStyle = rgba(palette.colors[0], 1);
    ctx.fillRect(0, 0, size, size);

    var cols = 7;
    var rows = 7;
    var cellW = size / cols;
    var cellH = size / rows;
    var grid = [];

    for (var r = 0; r <= rows; r++) {
      var rowPts = [];
      for (var c = 0; c <= cols; c++) {
        var jx = c === 0 || c === cols ? 0 : (rng() - 0.5) * cellW * 0.65;
        var jy = r === 0 || r === rows ? 0 : (rng() - 0.5) * cellH * 0.65;
        rowPts.push({
          x: c * cellW + jx,
          y: r * cellH + jy,
        });
      }
      grid.push(rowPts);
    }

    for (var gy = 0; gy < rows; gy++) {
      for (var gx = 0; gx < cols; gx++) {
        var tl = grid[gy][gx];
        var tr = grid[gy][gx + 1];
        var br = grid[gy + 1][gx + 1];
        var bl = grid[gy + 1][gx];

        // Bias brighter facets toward upper sky and darker facets toward lower ground.
        var verticalRatio = gy / rows;
        var baseIdx = verticalRatio < 0.45 ? 2 + Math.floor(rng() * 3) : Math.floor(rng() * 3);
        var colorA = palette.colors[clamp(baseIdx, 0, palette.colors.length - 1)];
        var colorB = palette.colors[clamp(baseIdx + (rng() > 0.5 ? 1 : -1), 0, palette.colors.length - 1)];
        var shadeA = (rng() - 0.5) * 26;
        var shadeB = (rng() - 0.5) * 26;

        if (rng() > 0.5) {
          drawFacetPolygon(ctx, [tl, tr, br], rgba(colorA, 0.92, shadeA), rgba(palette.shadow, 0.14));
          drawFacetPolygon(ctx, [tl, br, bl], rgba(colorB, 0.9, shadeB), rgba(palette.shadow, 0.14));
        } else {
          drawFacetPolygon(ctx, [tl, tr, bl], rgba(colorA, 0.92, shadeA), rgba(palette.shadow, 0.14));
          drawFacetPolygon(ctx, [tr, br, bl], rgba(colorB, 0.9, shadeB), rgba(palette.shadow, 0.14));
        }
      }
    }
  }

  /**
   * Step 4: Munch-inspired undulating horizon & sky bands scaled by mood delta.
   */
  function drawMunchBands(ctx, size, palette, moodDelta, vortex, rng) {
    var waveAmp = 10 + clamp(moodDelta, 0, 10) * 5.2;
    var bandCount = 6;
    var phaseBase = rng() * Math.PI * 2;

    for (var b = 0; b < bandCount; b++) {
      var yBase = size * (0.14 + b * 0.115);
      var freq = 0.015 + b * 0.0025;
      var phase = phaseBase + b * 0.85;
      var thickness = 24 + rng() * 22;
      var col = palette.colors[(b + 1) % palette.colors.length];

      ctx.beginPath();
      for (var x = -10; x <= size + 10; x += 8) {
        var y =
          yBase +
          Math.sin(x * freq + phase) * waveAmp +
          Math.cos(x * freq * 1.9 - phase * 0.6) * (waveAmp * 0.35);
        if (x === -10) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      }
      ctx.lineWidth = thickness;
      ctx.lineCap = "round";
      ctx.strokeStyle = rgba(col, 0.34);
      ctx.stroke();

      // Thin expressive dark contour line echoing Munch woodcuts/paintings.
      ctx.lineWidth = 2.2;
      ctx.strokeStyle = rgba(palette.shadow, 0.24);
      ctx.stroke();
    }

    // Subtle glowing celestial disc near primary vortex.
    var sunGrad = ctx.createRadialGradient(vortex.x, vortex.y, 4, vortex.x, vortex.y, 72);
    sunGrad.addColorStop(0, rgba(palette.colors[4], 0.55));
    sunGrad.addColorStop(0.5, rgba(palette.accent, 0.22));
    sunGrad.addColorStop(1, rgba(palette.colors[2], 0));
    ctx.fillStyle = sunGrad;
    ctx.beginPath();
    ctx.arc(vortex.x, vortex.y, 72, 0, Math.PI * 2);
    ctx.fill();
  }

  function flowAngle(x, y, vortex1, vortex2, moodDelta, phase) {
    var dx1 = x - vortex1.x;
    var dy1 = y - vortex1.y;
    var dist1 = Math.sqrt(dx1 * dx1 + dy1 * dy1) + 28;
    var swirl1 = Math.atan2(dy1, dx1) + Math.PI / 2;
    var weight1 = 160 / dist1;

    var dx2 = x - vortex2.x;
    var dy2 = y - vortex2.y;
    var dist2 = Math.sqrt(dx2 * dx2 + dy2 * dy2) + 36;
    var swirl2 = Math.atan2(dy2, dx2) - Math.PI / 2;
    var weight2 = 110 / dist2;

    var waveSlope = Math.cos(x * 0.018 + phase) * (0.25 + moodDelta * 0.08);
    var vx = Math.cos(waveSlope) + Math.cos(swirl1) * weight1 + Math.cos(swirl2) * weight2;
    var vy = Math.sin(waveSlope) + Math.sin(swirl1) * weight1 + Math.sin(swirl2) * weight2;
    return Math.atan2(vy, vx);
  }

  function paintImpastoBatch(ctx, count, size, palette, vortex1, vortex2, moodDelta, phase, rng, alphaScale) {
    ctx.lineCap = "round";
    for (var i = 0; i < count; i++) {
      var x = rng() * size;
      var y = rng() * size;
      var angle = flowAngle(x, y, vortex1, vortex2, moodDelta, phase);
      var len = 10 + rng() * 16;
      var curve = (rng() - 0.5) * 0.55;
      var mx = x + Math.cos(angle + curve) * (len * 0.5);
      var my = y + Math.sin(angle + curve) * (len * 0.5);
      var ex = x + Math.cos(angle) * len;
      var ey = y + Math.sin(angle) * len;

      var colorIdx = Math.floor(rng() * palette.colors.length);
      var rgb = palette.colors[colorIdx];
      var shade = (rng() - 0.45) * 32;
      var width = 2.4 + rng() * 4.2;

      // Under-stroke shadow for tactile oil paint thickness.
      ctx.beginPath();
      ctx.moveTo(x + 1.2, y + 1.5);
      ctx.quadraticCurveTo(mx + 1.2, my + 1.5, ex + 1.2, ey + 1.5);
      ctx.lineWidth = width + 0.8;
      ctx.strokeStyle = rgba(palette.shadow, 0.24 * alphaScale);
      ctx.stroke();

      // Main impasto pigment stroke.
      ctx.beginPath();
      ctx.moveTo(x, y);
      ctx.quadraticCurveTo(mx, my, ex, ey);
      ctx.lineWidth = width;
      ctx.strokeStyle = rgba(rgb, (0.48 + rng() * 0.34) * alphaScale, shade);
      ctx.stroke();
    }
  }

  function strokeSilhouetteRim(ctx, palette) {
    ctx.lineWidth = 2.2;
    ctx.strokeStyle = rgba(palette.colors[4], 0.32);
    ctx.stroke();
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = rgba(palette.shadow, 0.45);
    ctx.stroke();
  }

  function drawHumanoidMotif(ctx, cx, cy, scale, mood, palette) {
    var slump = mood < 4 ? 10 : mood >= 7 ? -6 : 2;
    var fill = rgba(palette.shadow, 0.62);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(scale, scale);

    // Elongated Munch-style head.
    ctx.beginPath();
    ctx.ellipse(slump * 0.35, -62 + Math.abs(slump) * 0.3, 13, 17, slump * 0.015, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    strokeSilhouetteRim(ctx, palette);

    // Expressive cloaked torso & raised/drooped shoulders.
    ctx.beginPath();
    ctx.moveTo(-8, -44);
    ctx.bezierCurveTo(-30, -34 + slump * 0.6, -26, 20, -22, 68);
    ctx.lineTo(22, 68);
    ctx.bezierCurveTo(26, 18, 28, -32 + slump * 0.4, 9, -44);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();
    strokeSilhouetteRim(ctx, palette);

    // Subtle uplifted or cradling arms depending on mood.
    ctx.beginPath();
    if (mood >= 7) {
      ctx.moveTo(-18, -30);
      ctx.quadraticCurveTo(-36, -54, -28, -74);
      ctx.moveTo(18, -30);
      ctx.quadraticCurveTo(36, -54, 28, -74);
    } else {
      ctx.moveTo(-18, -28);
      ctx.quadraticCurveTo(-26, -8, -10, -2);
      ctx.moveTo(18, -28);
      ctx.quadraticCurveTo(26, -8, 10, -2);
    }
    ctx.lineWidth = 6;
    ctx.lineCap = "round";
    ctx.strokeStyle = rgba(palette.shadow, 0.56);
    ctx.stroke();

    ctx.restore();
  }

  function drawAnimalMotif(ctx, cx, cy, scale, subtype, palette) {
    var fill = rgba(palette.shadow, 0.6);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(scale, scale);

    if (subtype === "bird") {
      // Sweeping abstract bird in flight or perched profile.
      ctx.beginPath();
      ctx.moveTo(-54, -18);
      ctx.quadraticCurveTo(-18, -46, 0, -14);
      ctx.quadraticCurveTo(18, -48, 56, -16);
      ctx.quadraticCurveTo(24, -4, 8, 16);
      ctx.lineTo(-10, 34);
      ctx.quadraticCurveTo(-14, 10, -54, -18);
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      strokeSilhouetteRim(ctx, palette);
    } else {
      // Symbolic quadruped (wolf / cat) silhouette in profile.
      ctx.beginPath();
      ctx.moveTo(-42, 42);
      ctx.lineTo(-34, -4);
      ctx.quadraticCurveTo(-48, -18, -56, -10); // tail
      ctx.quadraticCurveTo(-42, -24, -22, -16); // back
      ctx.lineTo(14, -18);
      ctx.lineTo(22, -40); // neck
      ctx.lineTo(26, -56); // left ear
      ctx.lineTo(32, -45);
      ctx.lineTo(38, -55); // right ear
      ctx.lineTo(42, -38);
      ctx.lineTo(52, -30); // muzzle
      ctx.lineTo(36, -20);
      ctx.lineTo(32, 42); // front leg
      ctx.lineTo(20, 42);
      ctx.lineTo(18, 10);
      ctx.lineTo(-18, 12);
      ctx.lineTo(-26, 42); // hind leg
      ctx.closePath();
      ctx.fillStyle = fill;
      ctx.fill();
      strokeSilhouetteRim(ctx, palette);
    }

    ctx.restore();
  }

  function drawRobotMotif(ctx, cx, cy, scale, palette) {
    var fill = rgba(palette.shadow, 0.62);

    ctx.save();
    ctx.translate(cx, cy);
    ctx.scale(scale, scale);

    // Antenna.
    ctx.beginPath();
    ctx.moveTo(0, -64);
    ctx.lineTo(0, -78);
    ctx.lineWidth = 3;
    ctx.strokeStyle = rgba(palette.colors[4], 0.55);
    ctx.stroke();

    // Faceted octagonal head.
    drawFacetPolygon(
      ctx,
      [
        { x: -16, y: -64 },
        { x: 16, y: -64 },
        { x: 20, y: -42 },
        { x: 14, y: -34 },
        { x: -14, y: -34 },
        { x: -20, y: -42 },
      ],
      fill,
      rgba(palette.colors[4], 0.35)
    );

    // Visor glow.
    ctx.fillStyle = rgba(palette.accent, 0.72);
    ctx.fillRect(-10, -52, 20, 5);

    // Cezanne-faceted geometric torso.
    drawFacetPolygon(
      ctx,
      [
        { x: -28, y: -28 },
        { x: 28, y: -28 },
        { x: 22, y: 24 },
        { x: -22, y: 24 },
      ],
      fill,
      rgba(palette.colors[4], 0.32)
    );

    // Articulated geometric limbs.
    var limbPolygons = [
      [{ x: -40, y: -22 }, { x: -30, y: -22 }, { x: -34, y: 18 }, { x: -44, y: 14 }],
      [{ x: 30, y: -22 }, { x: 40, y: -22 }, { x: 44, y: 14 }, { x: 34, y: 18 }],
      [{ x: -18, y: 26 }, { x: -6, y: 26 }, { x: -8, y: 66 }, { x: -20, y: 66 }],
      [{ x: 6, y: 26 }, { x: 18, y: 26 }, { x: 20, y: 66 }, { x: 8, y: 66 }],
    ];
    var limbEdge = rgba(palette.colors[4], 0.28);
    for (var i = 0; i < limbPolygons.length; i++) {
      drawFacetPolygon(ctx, limbPolygons[i], fill, limbEdge);
    }

    ctx.restore();
  }

  function drawVignette(ctx, size, palette) {
    var grad = ctx.createRadialGradient(
      size * 0.5,
      size * 0.46,
      size * 0.28,
      size * 0.5,
      size * 0.5,
      size * 0.72
    );
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(1, rgba(palette.shadow, 0.36));
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, size, size);
  }

  /**
   * Render deterministic 400x400 painterly artwork for a mood timeline slide.
   *
   * @param {HTMLCanvasElement} canvas
   * @param {object} slide
   * @param {object} [prevSlide]
   */
  function renderMoodArt(canvas, slide, prevSlide) {
    if (!canvas || typeof canvas.getContext !== "function") {
      return;
    }
    canvas.width = CANVAS_SIZE;
    canvas.height = CANVAS_SIZE;

    var ctx = canvas.getContext("2d");
    if (!ctx) {
      return;
    }

    var safeSlide = slide && typeof slide === "object" ? slide : {};
    var mood = clamp(toFiniteNumber(safeSlide.mood, 5), 0, 10);
    var age = toFiniteNumber(safeSlide.age, 0);
    var eventsList = normalizeEvents(safeSlide.events);
    var notes = safeSlide.notes !== null && safeSlide.notes !== undefined ? String(safeSlide.notes).trim() : "";
    var emoji = safeSlide.emoji !== null && safeSlide.emoji !== undefined ? String(safeSlide.emoji) : "";

    var prevMood =
      prevSlide && typeof prevSlide === "object"
        ? clamp(toFiniteNumber(prevSlide.mood, mood), 0, 10)
        : mood;
    var moodDelta = Math.abs(mood - prevMood);

    var seed = buildSeed(safeSlide, mood, age, eventsList, notes);
    var rng = mulberry32(seed);
    var palette = buildPalette(mood);

    var vortex1 = {
      x: CANVAS_SIZE * (0.22 + rng() * 0.56),
      y: CANVAS_SIZE * (0.16 + rng() * 0.24),
    };
    var vortex2 = {
      x: CANVAS_SIZE * (0.15 + rng() * 0.7),
      y: CANVAS_SIZE * (0.28 + rng() * 0.3),
    };
    var flowPhase = rng() * Math.PI * 2;

    // 1. Cezanne geometric color-faceted ground/sky.
    drawCezanneFacets(ctx, CANVAS_SIZE, palette, rng);

    // 2. Munch undulating horizon & sky bands driven by mood delta.
    drawMunchBands(ctx, CANVAS_SIZE, palette, moodDelta, vortex1, rng);

    // 3. Van Gogh directional flow-field impasto brushstrokes.
    paintImpastoBatch(ctx, 880, CANVAS_SIZE, palette, vortex1, vortex2, moodDelta, flowPhase, rng, 1.0);

    // 4. Subtle abstract silhouette motif from keywords or deterministic hash.
    var motif = chooseMotif(eventsList, notes, emoji, rng);
    var motifX = CANVAS_SIZE * (0.34 + rng() * 0.32);
    var motifY = CANVAS_SIZE * (0.64 + rng() * 0.08);
    var motifScale = 0.95 + rng() * 0.22;

    if (motif.type === "robot") {
      drawRobotMotif(ctx, motifX, motifY, motifScale, palette);
    } else if (motif.type === "animal") {
      drawAnimalMotif(ctx, motifX, motifY + 12, motifScale, motif.animalSubtype, palette);
    } else {
      drawHumanoidMotif(ctx, motifX, motifY, motifScale, mood, palette);
    }

    // 5. Light top impasto veil + vignette so silhouette melts into the brushwork.
    paintImpastoBatch(ctx, 130, CANVAS_SIZE, palette, vortex1, vortex2, moodDelta, flowPhase, rng, 0.48);
    drawVignette(ctx, CANVAS_SIZE, palette);
  }

  root.renderMoodArt = renderMoodArt;
})(typeof window !== "undefined" ? window : typeof globalThis !== "undefined" ? globalThis : {});
