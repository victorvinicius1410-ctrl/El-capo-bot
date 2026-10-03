import { copyFileSync, cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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

// version.json: a aba aberta compara com o id embutido no bundle e recarrega
// quando há publicação nova (src/lib/appVersion.ts). O id vem do vite.config.ts.
const buildIdFile = resolve(root, ".build-id");
if (existsSync(staticOutputDir) && existsSync(buildIdFile)) {
  const build = readFileSync(buildIdFile, "utf8").trim();
  if (build) {
    writeFileSync(
      resolve(staticOutputDir, "version.json"),
      JSON.stringify({ build, built_at: new Date().toISOString() }) + "\n",
    );
  }
}

if (existsSync(staticOutputDir)) {
  rmSync(distClientDir, { recursive: true, force: true });
  cpSync(staticOutputDir, distClientDir, { recursive: true, force: true });
}
