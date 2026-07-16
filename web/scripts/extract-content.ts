import * as fs from "fs";
import * as path from "path";
import type {
  AgentVersion,
  VersionDiff,
  DocContent,
  VersionIndex,
  ChapterImage,
} from "../src/types/agent-data";
import { VERSION_META, VERSION_ORDER, LEARNING_PATH } from "../src/lib/constants";

const WEB_DIR = path.resolve(__dirname, "..");
const REPO_ROOT = path.resolve(WEB_DIR, "..");
const OUT_DIR = path.join(WEB_DIR, "src", "data", "generated");
const PUBLIC_DIR = path.join(WEB_DIR, "public");
const COURSE_ASSETS_DIR = path.join(PUBLIC_DIR, "course-assets");

type Locale = "en" | "zh" | "ja";

interface ChapterSource {
  id: string;
  part: string; // foundations, patterns, systems, infrastructure
  dirName: string;
  dirPath: string;
  codePath: string;
}

// Map subdirectories to unique lesson IDs (f01-f15, p16-p30, c01-c04, i01)
const LESSON_ID_MAP: Record<string, string> = {
  // Part 1: Foundations
  "part-1-foundations/01-state": "f01",
  "part-1-foundations/02-agent-loop": "f02",
  "part-1-foundations/03-prompting-structured-output": "f03",
  "part-1-foundations/04-tool-calling": "f04",
  "part-1-foundations/05-memory": "f05",
  "part-1-foundations/06-planning-task-decomposition": "f06",
  "part-1-foundations/07-multi-agent-coordination": "f07",
  "part-1-foundations/08-human-approval": "f08",
  "part-1-foundations/09-evaluation-self-reflection": "f09",
  "part-1-foundations/10-scheduling-long-running-tasks": "f10",
  "part-1-foundations/11-async-execution": "f11",
  "part-1-foundations/12-event-driven-agents": "f12",
  "part-1-foundations/13-state-machines": "f13",
  "part-1-foundations/14-error-handling": "f14",
  "part-1-foundations/15-persistence": "f15",

  // Part 2: Production Patterns
  "part-2-production-patterns/16-retry-patterns": "p16",
  "part-2-production-patterns/17-background-workers": "p17",
  "part-2-production-patterns/18-redis-checkpointing": "p18",
  "part-2-production-patterns/19-postgresql-state-store": "p19",
  "part-2-production-patterns/20-queues": "p20",
  "part-2-production-patterns/21-streaming": "p21",
  "part-2-production-patterns/22-observability": "p22",
  "part-2-production-patterns/23-docker": "p23",
  "part-2-production-patterns/24-deployment": "p24",
  "part-2-production-patterns/25-configuration": "p25",
  "part-2-production-patterns/26-monitoring": "p26",
  "part-2-production-patterns/27-evaluation-pipelines": "p27",
  "part-2-production-patterns/28-cost-tracking": "p28",
  "part-2-production-patterns/29-security": "p29",
  "part-2-production-patterns/30-scaling": "p30",

  // Part 3: Complete Systems
  "part-3-complete-systems/healthcare-platform": "c01",
  "part-3-complete-systems/customer-support": "c02",
  "part-3-complete-systems/research-assistant": "c03",
  "part-3-complete-systems/coding-agent": "c04",

  // Part 4: Infrastructure
  "part-4-infrastructure/local-compose-deployment": "i01",
};

function listRootChapters(): ChapterSource[] {
  const chapters: ChapterSource[] = [];
  const parts = [
    { dir: "part-1-foundations", label: "foundations" },
    { dir: "part-2-production-patterns", label: "patterns" },
    { dir: "part-3-complete-systems", label: "systems" },
    { dir: "part-4-infrastructure", label: "infrastructure" },
  ];

  for (const part of parts) {
    const partPath = path.join(REPO_ROOT, part.dir);
    if (!fs.existsSync(partPath)) continue;

    const subdirs = fs
      .readdirSync(partPath, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name);

    for (const subdir of subdirs) {
      const dirPath = path.join(partPath, subdir);
      const relativeKey = `${part.dir}/${subdir}`;
      const id = LESSON_ID_MAP[relativeKey];
      if (!id) continue;

      // Look for example.py in the directory
      const codePath = path.join(dirPath, "example.py");
      chapters.push({
        id,
        part: part.label,
        dirName: subdir,
        dirPath,
        codePath,
      });
    }
  }

  // Sort chapters based on VERSION_ORDER to ensure deterministic index construction
  const orderMap = new Map(VERSION_ORDER.map((id, idx) => [id, idx]));
  chapters.sort((a, b) => (orderMap.get(a.id as any) ?? 99) - (orderMap.get(b.id as any) ?? 99));

  return chapters;
}

function extractClasses(
  lines: string[]
): { name: string; startLine: number; endLine: number }[] {
  const classes: { name: string; startLine: number; endLine: number }[] = [];
  const classPattern = /^class\s+(\w+)/;

  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(classPattern);
    if (!match) continue;

    const name = match[1];
    const startLine = i + 1;
    let endLine = lines.length;
    for (let j = i + 1; j < lines.length; j++) {
      if (
        lines[j].match(/^class\s/) ||
        lines[j].match(/^def\s/) ||
        (lines[j].match(/^\S/) &&
          lines[j].trim() !== "" &&
          !lines[j].startsWith("#") &&
          !lines[j].startsWith("@"))
      ) {
        endLine = j;
        break;
      }
    }
    classes.push({ name, startLine, endLine });
  }

  return classes;
}

function extractFunctions(
  lines: string[]
): { name: string; signature: string; startLine: number }[] {
  const functions: { name: string; signature: string; startLine: number }[] = [];
  const funcPattern = /^def\s+(\w+)\((.*?)\)/;

  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(funcPattern);
    if (!match) continue;
    functions.push({
      name: match[1],
      signature: `def ${match[1]}(${match[2]})`,
      startLine: i + 1,
    });
  }

  return functions;
}

function extractTools(source: string): string[] {
  const toolPattern = /"name"\s*:\s*"([\w-]+)"/g;
  const tools = new Set<string>();
  let match;
  while ((match = toolPattern.exec(source)) !== null) {
    tools.add(match[1]);
  }
  return Array.from(tools);
}

function countLoc(lines: string[]): number {
  return lines.filter((line) => {
    const trimmed = line.trim();
    return trimmed !== "" && !trimmed.startsWith("#");
  }).length;
}

function titleFromMarkdown(content: string, fallback: string): string {
  const titleMatch = content.match(/^#\s+(.+)$/m);
  return titleMatch ? titleMatch[1] : fallback;
}

function cleanCourseAssets() {
  fs.rmSync(COURSE_ASSETS_DIR, { recursive: true, force: true });
  fs.mkdirSync(COURSE_ASSETS_DIR, { recursive: true });
}

function copyChapterAssets(chapter: ChapterSource): ChapterImage[] {
  const imagesDir = path.join(chapter.dirPath, "images");
  if (!fs.existsSync(imagesDir)) return [];

  const outDir = path.join(COURSE_ASSETS_DIR, chapter.dirName);
  fs.mkdirSync(outDir, { recursive: true });
  fs.cpSync(imagesDir, outDir, { recursive: true });

  return fs
    .readdirSync(imagesDir)
    .filter((filename) => filename.endsWith(".svg"))
    .filter((filename) => !filename.includes(".en.") && !filename.includes(".ja."))
    .sort()
    .map((filename) => ({
      src: `/course-assets/${chapter.dirName}/${filename}`,
      alt: filename.replace(/\.svg$/, "").replace(/-/g, " "),
    }));
}

function localeReadmeName(locale: Locale): string {
  if (locale === "en") return "README.md";
  return `README.${locale}.md`;
}

function rewriteChapterMarkdown(
  content: string,
  chapter: ChapterSource,
  locale: Locale
): string {
  let next = content;

  next = next.replace(
    /^\[中文\]\(README\.md\)\s*.\s*\[English\]\(README\.en\.md\)\s*.\s*\[日本語\]\(README\.ja\.md\)\n\n?/m,
    ""
  );

  next = next.replace(
    /(!\[[^\]]*\]\()images\/([^)]+)(\))/g,
    `$1/course-assets/${chapter.dirName}/$2$3`
  );

  return next;
}

function buildRootVersions(chapters: ChapterSource[]): AgentVersion[] {
  return chapters.map((chapter) => {
    const source = fs.existsSync(chapter.codePath)
      ? fs.readFileSync(chapter.codePath, "utf-8")
      : "";
    const lines = source.split("\n");
    const meta = VERSION_META[chapter.id];

    return {
      id: chapter.id,
      filename: `${chapter.part}/${chapter.dirName}/example.py`,
      title: meta?.title ?? chapter.dirName,
      subtitle: meta?.subtitle ?? "",
      loc: countLoc(lines),
      tools: extractTools(source),
      newTools: [] as string[],
      coreAddition: meta?.coreAddition ?? "",
      keyInsight: meta?.keyInsight ?? "",
      classes: extractClasses(lines),
      functions: extractFunctions(lines),
      layer: meta?.layer ?? "foundations",
      source,
      images: copyChapterAssets(chapter),
    };
  });
}

function buildRootDocs(chapters: ChapterSource[]): DocContent[] {
  const docs: DocContent[] = [];
  const locales: Locale[] = ["en"]; // English only

  for (const chapter of chapters) {
    for (const locale of locales) {
      const filename = localeReadmeName(locale);
      const filePath = path.join(chapter.dirPath, filename);
      if (!fs.existsSync(filePath)) continue;

      const raw = fs.readFileSync(filePath, "utf-8");
      const content = rewriteChapterMarkdown(raw, chapter, locale);
      docs.push({
        version: chapter.id,
        locale,
        title: titleFromMarkdown(content, filename),
        content,
      });
    }
  }

  return docs;
}

function computeNewTools(versions: AgentVersion[]) {
  for (let i = 0; i < versions.length; i++) {
    const prev = i > 0 ? new Set(versions[i - 1].tools) : new Set<string>();
    versions[i].newTools = versions[i].tools.filter((tool) => !prev.has(tool));
  }
}

function buildDiffs(versions: AgentVersion[]): VersionDiff[] {
  const diffs: VersionDiff[] = [];
  const versionMap = new Map(versions.map((version) => [version.id, version]));

  for (let i = 1; i < LEARNING_PATH.length; i++) {
    const fromId = LEARNING_PATH[i - 1];
    const toId = LEARNING_PATH[i];
    const fromVer = versionMap.get(fromId);
    const toVer = versionMap.get(toId);
    if (!fromVer || !toVer) continue;

    const fromClassNames = new Set(fromVer.classes.map((cls) => cls.name));
    const fromFuncNames = new Set(fromVer.functions.map((fn) => fn.name));
    const fromToolNames = new Set(fromVer.tools);

    diffs.push({
      from: fromId,
      to: toId,
      newClasses: toVer.classes
        .map((cls) => cls.name)
        .filter((name) => !fromClassNames.has(name)),
      newFunctions: toVer.functions
        .map((fn) => fn.name)
        .filter((name) => !fromFuncNames.has(name)),
      newTools: toVer.tools.filter((tool) => !fromToolNames.has(tool)),
      locDelta: toVer.loc - fromVer.loc,
    });
  }

  return diffs;
}

function main() {
  console.log("Extracting Production AI Course Content...");
  console.log(`  Repo root: ${REPO_ROOT}`);

  cleanCourseAssets();

  const chapters = listRootChapters();

  console.log(`  Found ${chapters.length} active chapters`);

  const versions = buildRootVersions(chapters);
  const docs = buildRootDocs(chapters);

  computeNewTools(versions);
  const diffs = buildDiffs(versions);

  fs.mkdirSync(OUT_DIR, { recursive: true });

  const index: VersionIndex = { versions, diffs };
  fs.writeFileSync(path.join(OUT_DIR, "versions.json"), JSON.stringify(index, null, 2));
  fs.writeFileSync(path.join(OUT_DIR, "docs.json"), JSON.stringify(docs, null, 2));

  console.log("\nExtraction complete:");
  console.log(`  ${versions.length} versions generated`);
  console.log(`  ${diffs.length} diffs generated`);
  console.log(`  ${docs.length} docs generated`);
  for (const version of versions) {
    console.log(
      `    ${version.id}: ${version.loc} LOC, ${version.tools.length} tools, ${version.classes.length} classes, ${version.functions.length} functions`
    );
  }
}

main();
