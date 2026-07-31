import { test } from "@playwright/test";
import * as fs from "fs";

// Generate handwriting-style test images (white bg + grid + cursive text)
// for the REAL vision-judge verification. Output: /tmp/hw_img_*.txt (dataURLs).

test("generate handwriting test images", async ({ page }) => {
  await page.goto("about:blank");
  const english = await page.evaluate(() => {
    const canvas = document.createElement("canvas");
    canvas.width = 760;
    canvas.height = 180;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, 760, 180);
    // four-line grid like the real component
    ctx.strokeStyle = "#94a3b8";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(4, 122); ctx.lineTo(756, 122); ctx.stroke();
    ctx.strokeStyle = "#cbd5e1";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([7, 6]);
    for (const y of [14, 65, 166]) { ctx.beginPath(); ctx.moveTo(4, y); ctx.lineTo(756, y); ctx.stroke(); }
    ctx.setLineDash([]);
    // childish cursive-ish writing: italic serif, slightly wobbly baseline
    ctx.fillStyle = "#1a1a1a";
    ctx.font = "italic 700 64px 'Comic Sans MS', 'Segoe Script', cursive";
    const letters = "apple".split("");
    let x = 180;
    for (const ch of letters) {
      ctx.save();
      ctx.translate(x, 122 + (Math.random() * 6 - 3));
      ctx.rotate((Math.random() * 8 - 4) * Math.PI / 180);
      ctx.fillText(ch, 0, 0);
      ctx.restore();
      x += 62;
    }
    return canvas.toDataURL("image/png");
  });
  fs.writeFileSync("/tmp/hw_img_english.txt", english);

  const chinese = await page.evaluate(() => {
    const cell = 110;
    const n = 4;
    const canvas = document.createElement("canvas");
    canvas.width = cell * n;
    canvas.height = cell;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, cell * n, cell);
    for (let i = 0; i < n; i += 1) {
      const x0 = i * cell;
      ctx.strokeStyle = "#c8a87c";
      ctx.lineWidth = 2;
      ctx.strokeRect(x0 + 1, 1, cell - 2, cell - 2);
      ctx.strokeStyle = "#e8dcc8";
      ctx.lineWidth = 1;
      ctx.setLineDash([6, 5]);
      ctx.beginPath();
      ctx.moveTo(x0 + cell / 2, 0); ctx.lineTo(x0 + cell / 2, cell);
      ctx.moveTo(x0, cell / 2); ctx.lineTo(x0 + cell, cell / 2);
      ctx.moveTo(x0, 0); ctx.lineTo(x0 + cell, cell);
      ctx.moveTo(x0 + cell, 0); ctx.lineTo(x0, cell);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    ctx.fillStyle = "#1a1a1a";
    ctx.font = "700 72px 'Kaiti SC', 'KaiTi', 'STKaiti', serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("苹", cell * 0.5 + 3, cell / 2 + 2);
    ctx.fillText("果", cell * 1.5 - 2, cell / 2 - 3);
    return canvas.toDataURL("image/png");
  });
  fs.writeFileSync("/tmp/hw_img_chinese.txt", chinese);
});
