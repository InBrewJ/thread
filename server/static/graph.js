// Graph coordinate calculations and rendering helpers for Timeline

function addYearsToDate(dateStr, years) {
  if (!dateStr) return "";
  const d = new Date(dateStr);
  if (isNaN(d.getTime())) return "";
  const totalDays = years * 365.2425;
  const target = new Date(d.getTime() + totalDays * 24 * 60 * 60 * 1000);
  return target.toLocaleDateString("en-GB", { month: "short", year: "numeric" });
}

function getMoodColor(mood) {
  if (mood <= 2) return "#3b82f6"; // Deep blue (Depression)
  if (mood <= 4) return "#60a5fa"; // Light blue (Low)
  if (mood <= 6) return "#10b981"; // Emerald green (Stable / Baseline)
  if (mood <= 8) return "#f59e0b"; // Amber (Elevated / Hypomania)
  return "#ef4444"; // Red / Coral (Manic)
}

function getMoodLabel(mood) {
  if (mood <= 1) return "Severe Depression";
  if (mood <= 3) return "Low / Depressed";
  if (mood <= 6) return "Stable / Balanced";
  if (mood <= 8) return "Elevated / Hypomanic";
  return "Manic / Crisis";
}

var prepareAllPoints = function (slides, width, height, padding) {
  if (!slides || slides.length === 0) return [];

  const w = width || 300;
  const h = height || 150;
  const padX = padding ? padding.x : 15;
  const padY = padding ? padding.y : 15;

  const minAge = Math.min(...slides.map((s) => parseFloat(s.age)));
  const maxAge = Math.max(...slides.map((s) => parseFloat(s.age)));
  const ageSpan = maxAge > minAge ? maxAge - minAge : 1;

  const minMood = 0;
  const maxMood = 10;
  const moodSpan = maxMood - minMood;

  return slides.map((slide, index) => {
    const age = parseFloat(slide.age);
    const mood = parseFloat(slide.mood);

    // Scale age along X axis
    const x = padX + ((age - minAge) / ageSpan) * (w - 2 * padX);

    // Invert mood along Y axis (0 at bottom, 10 at top)
    const y = (h - padY) - ((mood - minMood) / moodSpan) * (h - 2 * padY);

    return {
      x: Math.round(x * 10) / 10,
      y: Math.round(y * 10) / 10,
      age: age,
      mood: mood,
      emoji: slide.emoji || "",
      events: slide.events || [],
      notes: slide.notes || "",
      index: index,
    };
  });
};

function calculateChaoticZones(points) {
  if (!points || points.length < 5) return [];

  const n = points.length;
  const ages = points.map(p => parseFloat(p.age));
  const moods = points.map(p => parseFloat(p.mood));

  // Compute segment volatility (rate of mood change per year of age)
  const segVol = [];
  for (let i = 0; i < n - 1; i++) {
    const dt = Math.max(0.08, ages[i + 1] - ages[i]);
    const dm = Math.abs(moods[i + 1] - moods[i]);
    // Volatility rate for swings >= 2.0
    segVol.push(dm >= 2.0 ? (dm / dt) : 0);
  }

  // Point volatility = maximum of incoming and outgoing segment volatility
  const ptVol = [segVol[0] || 0];
  for (let i = 1; i < n - 1; i++) {
    ptVol.push(Math.max(segVol[i - 1], segVol[i]));
  }
  ptVol.push(segVol[segVol.length - 1] || 0);

  // Relative baseline: median of active swing rates across this curve
  const activeVols = ptVol.filter(v => v > 0);
  if (activeVols.length < 3) return [];

  activeVols.sort((a, b) => a - b);
  const medianVol = activeVols[Math.floor(activeVols.length / 2)];
  const relativeThreshold = Math.max(4.0, medianVol * 0.85);

  // Mark points where volatility meets relative threshold AND local mood swing range >= 3.5
  const isChaotic = [];
  for (let i = 0; i < n; i++) {
    const start = Math.max(0, i - 1);
    const end = Math.min(n, i + 2);
    const localMoods = moods.slice(start, end);
    const localRange = Math.max(...localMoods) - Math.min(...localMoods);
    isChaotic.push(ptVol[i] >= relativeThreshold && localRange >= 3.5);
  }

  // Group into clusters, bridging single-step gaps within 1.2 years
  const rawClusters = [];
  let cur = [];
  for (let i = 0; i < n; i++) {
    if (isChaotic[i]) {
      cur.push(i);
    } else {
      if (cur.length > 0 && i + 1 < n && isChaotic[i + 1] && (ages[i + 1] - ages[cur[cur.length - 1]] <= 1.2)) {
        cur.push(i); // bridge intermediate transition point
      } else {
        if (cur.length >= 3) {
          rawClusters.push(cur);
        }
        cur = [];
      }
    }
  }
  if (cur.length >= 3) {
    rawClusters.push(cur);
  }

  return rawClusters.map(clusterIndices => {
    const clusterPoints = clusterIndices.map(idx => points[idx]);
    const xs = clusterPoints.map(p => p.x);
    const ys = clusterPoints.map(p => p.y);
    const clusterAges = clusterPoints.map(p => p.age);
    const clusterMoods = clusterPoints.map(p => p.mood);

    return {
      indices: clusterIndices,
      minX: Math.min(...xs),
      maxX: Math.max(...xs),
      minY: Math.min(...ys),
      maxY: Math.max(...ys),
      minAge: Math.min(...clusterAges),
      maxAge: Math.max(...clusterAges),
      minMood: Math.min(...clusterMoods),
      maxMood: Math.max(...clusterMoods),
      pointsCount: clusterPoints.length
    };
  });
}

function drawSubtleRoundedBox(ctx, x, y, width, height, radius) {
  if (width <= 0 || height <= 0) return;
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  if (ctx.roundRect) {
    ctx.roundRect(x, y, width, height, r);
  } else {
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + width - r, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + r);
    ctx.lineTo(x + width, y + height - r);
    ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    ctx.lineTo(x + r, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - r);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }
}

function renderTimelineCanvas(canvas, slides, activeIndex, options) {
  if (!canvas || !slides || slides.length === 0) return;

  const opt = options || {};
  const isFull = opt.isFull !== false;
  const tension = opt.tension !== undefined ? opt.tension : 0.25;

  // Sync internal resolution with CSS pixel size
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const displayWidth = Math.max(rect.width || canvas.width || 400, 300);
  const displayHeight = Math.max(rect.height || canvas.height || 200, 160);

  canvas.width = displayWidth * dpr;
  canvas.height = displayHeight * dpr;

  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, displayWidth, displayHeight);

  // Background subtle guide zones
  const padY = 20;
  const padX = 25;
  const chartHeight = displayHeight - 2 * padY;
  const plotWidth = displayWidth - 2 * padX;

  // Baseline grid lines
  const baselineMoods = [
    { mood: 0, label: "0", color: "rgba(59, 130, 246, 0.15)" },
    { mood: 5, label: "5 (Baseline)", color: "rgba(16, 185, 129, 0.2)" },
    { mood: 10, label: "10", color: "rgba(239, 68, 68, 0.15)" },
  ];

  ctx.lineWidth = 1;
  baselineMoods.forEach((bm) => {
    const y = (displayHeight - padY) - (bm.mood / 10) * chartHeight;
    ctx.strokeStyle = bm.color;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(padX, y);
    ctx.lineTo(displayWidth - padX, y);
    ctx.stroke();
    ctx.setLineDash([]);
  });

  const allPoints = prepareAllPoints(slides, displayWidth, displayHeight, { x: padX, y: padY });
  const pointsToDraw = isFull ? allPoints : allPoints.slice(0, (activeIndex !== undefined ? activeIndex : slides.length - 1) + 1);

  if (pointsToDraw.length < 2) {
    if (pointsToDraw.length === 1) {
      // Draw single point
      const p = pointsToDraw[0];
      ctx.fillStyle = getMoodColor(p.mood);
      ctx.beginPath();
      ctx.arc(p.x, p.y, 6, 0, Math.PI * 2);
      ctx.fill();
    }
    return;
  }

  // Draw MonMon Spending Surge Overlay (if available and enabled)
  if (opt.showSpendingOverlay !== false && opt.spendingData && opt.spendingData.available && opt.spendingData.months && opt.spendingData.months.length > 0) {
    try {
      const surgeMonths = opt.spendingData.months.filter(m => m.is_surge);
      if (surgeMonths.length > 0) {
        const timelineMinAge = Math.min(...slides.map(s => parseFloat(s.age)));
        const timelineMaxAge = Math.max(...slides.map(s => parseFloat(s.age)));
        const timelineSpan = Math.max(0.1, timelineMaxAge - timelineMinAge);

        const surgeAges = surgeMonths.map(m => m.age);
        const minSurgeAge = Math.min(...surgeAges);
        const maxSurgeAge = Math.max(...surgeAges);

        // Window slightly expanded to cover surrounding transition
        const windowMinAge = Math.max(timelineMinAge, minSurgeAge - 0.2);
        const windowMaxAge = Math.min(timelineMaxAge, maxSurgeAge + 0.2);

        const bx1 = padX + ((windowMinAge - timelineMinAge) / timelineSpan) * plotWidth;
        const bx2 = padX + ((windowMaxAge - timelineMinAge) / timelineSpan) * plotWidth;

        const bx = Math.max(padX - 4, Math.min(bx1, bx2) - 8);
        const bw = Math.max(48, Math.abs(bx2 - bx1) + 16);

        // Find Y range of points in this age window to bound the box neatly around the curve
        const windowPoints = allPoints.filter(p => p.age >= windowMinAge - 0.35 && p.age <= windowMaxAge + 0.35);
        let by, bh;
        if (windowPoints.length > 0) {
          const ys = windowPoints.map(p => p.y);
          by = Math.max(padY, Math.min(...ys) - 16);
          const maxY = Math.min(displayHeight - padY, Math.max(...ys) + 16);
          bh = Math.max(40, maxY - by);
        } else {
          by = padY + 10;
          bh = chartHeight - 20;
        }

        ctx.save();
        // 1. Subtle warm amber/gold tinted box
        drawSubtleRoundedBox(ctx, bx, by, bw, bh, 6);
        ctx.fillStyle = "rgba(245, 158, 11, 0.09)";
        ctx.fill();

        // 2. Subtle dashed amber border
        ctx.strokeStyle = "rgba(245, 158, 11, 0.55)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // 3. Header badge
        const maxSurgePct = Math.round((Math.max(...surgeMonths.map(m => m.surge_ratio)) - 1) * 100);
        ctx.fillStyle = "#fbbf24";
        ctx.font = "600 9px system-ui, -apple-system, sans-serif";
        ctx.fillText(`💳 Spend Surge (+${maxSurgePct}%)`, bx + 6, Math.max(by + 11, padY + 10));

        // 4. Highlight slide dots that fall inside the spending surge window
        windowPoints.forEach(p => {
          ctx.strokeStyle = "#f59e0b";
          ctx.lineWidth = 2;
          ctx.beginPath();
          ctx.arc(p.x, p.y, 8, 0, Math.PI * 2);
          ctx.stroke();
        });

        ctx.restore();
      }
    } catch (err) {
      console.warn("[Thread] Error rendering MonMon spend surge zone:", err);
    }
  }

  // Draw subtle boxes around chaotic zones on full curve previews
  if (isFull && opt.showChaoticZones !== false) {
    const chaoticZones = calculateChaoticZones(allPoints);
    chaoticZones.forEach((zone) => {
      const padBoxX = 10;
      const padBoxY = 8;
      const bx = Math.max(padX - 4, zone.minX - padBoxX);
      const by = Math.max(padY - 4, zone.minY - padBoxY);
      const bw = Math.min(displayWidth - padX + 4 - bx, (zone.maxX - zone.minX) + 2 * padBoxX);
      const bh = Math.min(displayHeight - padY + 4 - by, (zone.maxY - zone.minY) + 2 * padBoxY);

      if (bw > 0 && bh > 0) {
        ctx.save();
        // Subtle tinted background
        drawSubtleRoundedBox(ctx, bx, by, bw, bh, 6);
        ctx.fillStyle = "rgba(244, 63, 94, 0.05)";
        ctx.fill();

        // Subtle dashed border
        ctx.strokeStyle = "rgba(244, 63, 94, 0.35)";
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Subtle header label
        ctx.fillStyle = "rgba(244, 63, 94, 0.65)";
        ctx.font = "600 9px system-ui, -apple-system, sans-serif";
        ctx.fillText("⚡ Chaotic Zone", bx + 6, Math.max(by + 11, padY + 8));
        ctx.restore();
      }
    });
  }

  // Draw smooth spline curve
  const flatCoords = pointsToDraw.flatMap((p) => [p.x, p.y]);
  ctx.lineWidth = opt.lineWidth || 3;
  ctx.strokeStyle = opt.strokeColor || "#38bdf8";

  // Use cardinal spline curve if available
  if (typeof drawCurve === "function") {
    ctx.beginPath();
    drawCurve(ctx, flatCoords, tension, false, 24, false);
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.moveTo(flatCoords[0], flatCoords[1]);
    for (let i = 2; i < flatCoords.length; i += 2) {
      ctx.lineTo(flatCoords[i], flatCoords[i + 1]);
    }
    ctx.stroke();
  }

  // Draw point dots
  pointsToDraw.forEach((p, idx) => {
    const isActive = activeIndex !== undefined && idx === activeIndex;
    ctx.fillStyle = getMoodColor(p.mood);
    ctx.beginPath();
    ctx.arc(p.x, p.y, isActive ? 7 : 3.5, 0, Math.PI * 2);
    ctx.fill();

    if (isActive) {
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
      ctx.stroke();
    }
  });
}
