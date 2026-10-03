import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import tsconfigPaths from "vite-tsconfig-paths";
import { tanstackStart } from "@tanstack/react-start/plugin/vite";
import { nitro } from "nitro/vite";

// Id desta publicação. Vai para dentro do bundle (`__ELCAPO_BUILD_ID__`) e para
// o `version.json` (escrito pelo scripts/postbuild-static.mjs a partir do
// `.build-id`): a aba compara os dois e recarrega quando há publicação nova.
// Ver src/lib/appVersion.ts.
const BUILD_ID = process.env.ELCAPO_BUILD_ID ?? Date.now().toString(36);

export default defineConfig(({ command }) => {
  if (command === "build") {
    writeFileSync(fileURLToPath(new URL("./.build-id", import.meta.url)), BUILD_ID);
  }
  return {
    define: {
      __ELCAPO_BUILD_ID__: JSON.stringify(command === "build" ? BUILD_ID : ""),
    },
    plugins: [
      tanstackStart({
        server: { entry: "server" },
        prerender: {
          enabled: true,
          crawlLinks: true,
          concurrency: 8,
          failOnError: false,
        },
      }),
      nitro({ preset: "vercel" }),
      react(),
      tsconfigPaths(),
      tailwindcss(),
    ],
  };
});
