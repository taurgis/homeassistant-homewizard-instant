import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "..");

const packageJsonPath = path.join(repoRoot, "package.json");
const manifestPath = path.join(
  repoRoot,
  "custom_components",
  "homewizard_instant",
  "manifest.json",
);

const packageJson = JSON.parse(await readFile(packageJsonPath, "utf8"));
const nextVersion = packageJson.version;

if (typeof nextVersion !== "string" || nextVersion.length === 0) {
  throw new Error("package.json must define a non-empty version string");
}

const manifestContents = await readFile(manifestPath, "utf8");
const manifestVersionPattern = /^(  "version": ").*(",?)$/m;

if (!manifestVersionPattern.test(manifestContents)) {
  throw new Error("Could not find an integration version in manifest.json");
}

const nextManifestContents = manifestContents.replace(
  manifestVersionPattern,
  `$1${nextVersion}$2`,
);

await writeFile(manifestPath, nextManifestContents, "utf8");

console.log(`Synced release version ${nextVersion} to manifest.json`);