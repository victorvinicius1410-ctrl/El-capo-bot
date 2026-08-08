import { copyFileSync, cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";

const root = process.cwd();
const source = resolve(root, "public", ".htaccess");
const staticOutputDir = resolve(root, ".vercel", "output", "static");
const distClientDir = resolve(root, "dist", "client");
const htaccessTargets = [
  resolve(staticOutputDir, ".htaccess"),
  resolve(distClientDir, ".htaccess"),
];

if (existsSync(source)) {
  for (const target of htaccessTargets) {
    mkdirSync(dirname(target), { recursive: true });
    copyFileSync(source, target);
  }
}

if (existsSync(staticOutputDir)) {
  rmSync(distClientDir, { recursive: true, force: true });
  cpSync(staticOutputDir, distClientDir, { recursive: true, force: true });
}
