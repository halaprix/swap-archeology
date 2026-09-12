import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, extname, resolve as resolvePath } from "node:path";
import ts from "typescript";

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith(".") && !extname(specifier)) {
    const candidate = resolvePath(dirname(fileURLToPath(context.parentURL)), specifier);
    for (const extension of [".ts", ".tsx", ".js"]) {
      try {
        await readFile(`${candidate}${extension}`);
        return nextResolve(pathToFileURL(`${candidate}${extension}`).href, context);
      } catch {
        // Try the next source extension.
      }
    }
  }
  return nextResolve(specifier, context);
}

export async function load(url, context, nextLoad) {
  if (url.endsWith(".json")) {
    const source = await readFile(new URL(url), "utf8");
    return { format: "module", source: `export default ${source};`, shortCircuit: true };
  }
  if (url.endsWith(".ts") || url.endsWith(".tsx")) {
    const source = await readFile(new URL(url), "utf8");
    const output = ts.transpileModule(source, {
      compilerOptions: {
        target: ts.ScriptTarget.ES2022,
        module: ts.ModuleKind.ESNext,
        importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
        sourceMap: false,
      },
      fileName: url,
    });
    return { format: "module", source: output.outputText, shortCircuit: true };
  }
  return nextLoad(url, context);
}
