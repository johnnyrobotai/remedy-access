import { defineConfig } from "vite";
import { resolve } from "path";

export default defineConfig({
  build: {
    lib: {
      entry: resolve(__dirname, "src/docbox.ts"),
      formats: ["iife"],
      name: "DocBox",
      fileName: () => "docbox.js",
    },
    outDir: "dist",
    emptyOutDir: true,
    minify: "esbuild",
    target: "es2018",
    rollupOptions: {
      output: {
        extend: true,
      },
    },
  },
});
