const CACHE_KEY = 'refactoriq_source_cache_v1';
const CONTEXT_KEY = 'refactoriq_source_context_v1';
const MAX_CACHE_BYTES = 3_500_000;
const MAX_FILES = 250;
const SUPPORTED_EXTENSIONS = new Set(['.py', '.java', '.c', '.h', '.cpp', '.hpp', '.cc', '.cxx', '.js', '.ts', '.cs']);

function normalizePath(value) {
  return String(value || '')
    .replace(/\\/g, '/')
    .replace(/^\.\//, '')
    .replace(/^\/+/, '')
    .replace(/\/+/g, '/')
    .trim();
}

function matchKey(value) {
  return normalizePath(value).toLowerCase();
}

function pathBaseName(value) {
  return matchKey(value).split('/').filter(Boolean).pop() || '';
}

function extensionOf(path) {
  const name = pathBaseName(path);
  const index = name.lastIndexOf('.');
  return index >= 0 ? name.slice(index).toLowerCase() : '';
}

export function isSupportedSourcePath(path) {
  return SUPPORTED_EXTENSIONS.has(extensionOf(path));
}

function readJson(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key) || '') || fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

function bytesOf(text) {
  return new Blob([String(text || '')]).size;
}

export function saveSourceContext(context = {}) {
  const existing = readJson(CONTEXT_KEY, {});
  writeJson(CONTEXT_KEY, {
    ...existing,
    ...context,
    savedAt: new Date().toISOString(),
  });
}

export function getSourceContext() {
  return readJson(CONTEXT_KEY, {});
}

export function cacheSourceFiles(files, context = {}) {
  if (!Array.isArray(files) || !files.length) return { saved: 0, skipped: files?.length || 0 };

  const existing = readJson(CACHE_KEY, []);
  const byPath = new Map(existing.map(file => [matchKey(file.path), file]));
  let saved = 0;

  files.forEach(file => {
    const path = normalizePath(file.path || file.file_name || file.name);
    const source = String(file.source_code ?? file.code ?? file.source ?? '');
    if (!path || !source || !isSupportedSourcePath(path)) return;

    byPath.set(matchKey(path), {
      path,
      source_code: source,
      language: file.language || '',
      origin: file.origin || context.origin || 'browser_cache',
      repoName: context.repoName || file.repoName || '',
      repoUrl: context.repoUrl || file.repoUrl || '',
      savedAt: new Date().toISOString(),
    });
    saved += 1;
  });

  const trimmed = [...byPath.values()]
    .sort((a, b) => String(b.savedAt).localeCompare(String(a.savedAt)))
    .slice(0, MAX_FILES);

  const kept = [];
  let totalBytes = 0;
  for (const file of trimmed) {
    const size = bytesOf(file.source_code);
    if (totalBytes + size > MAX_CACHE_BYTES) continue;
    totalBytes += size;
    kept.push(file);
  }

  writeJson(CACHE_KEY, kept);
  if (context.repoName || context.repoUrl || context.origin) saveSourceContext(context);

  return { saved, retained: kept.length, bytes: totalBytes };
}

function findFlexible(cached, requestedPath) {
  const requested = matchKey(requestedPath);
  const requestedBase = pathBaseName(requestedPath);
  let match = cached.find(file => matchKey(file.path) === requested);
  if (match) return match;

  match = cached.find(file => {
    const cachedPath = matchKey(file.path);
    return cachedPath.endsWith(`/${requested}`) || requested.endsWith(`/${cachedPath}`);
  });
  if (match) return match;

  const baseMatches = cached.filter(file => pathBaseName(file.path) === requestedBase);
  return baseMatches.length === 1 ? baseMatches[0] : null;
}

export function getCachedSourceFiles(filePaths) {
  const cached = readJson(CACHE_KEY, []);
  const files = [];
  const missing = [];

  (filePaths || []).forEach(path => {
    const match = findFlexible(cached, path);
    if (match) {
      files.push({
        ...match,
        file_name: normalizePath(path),
        source_mode: 'raw',
        origin: match.origin || 'browser_cache',
      });
    } else {
      missing.push(path);
    }
  });

  return {
    files,
    missing,
    imported: files.length,
    source: 'browser_cache',
  };
}

function parseGithubRepoUrl(repoUrl) {
  const match = String(repoUrl || '').match(/^https:\/\/github\.com\/([^/\s]+)\/([^/\s#?]+)(?:[/?#]|$)/i);
  if (!match) return null;
  return {
    owner: match[1],
    repo: match[2].replace(/\.git$/i, ''),
  };
}

async function fetchGithubJson(url) {
  const response = await fetch(url, { headers: { Accept: 'application/vnd.github+json' } });
  if (!response.ok) throw new Error(`GitHub request failed with HTTP ${response.status}.`);
  return response.json();
}

function findGithubTreePath(tree, requestedPath) {
  const requested = matchKey(requestedPath);
  const requestedBase = pathBaseName(requestedPath);
  const blobs = (tree || []).filter(item => item?.type === 'blob' && isSupportedSourcePath(item.path));

  let match = blobs.find(item => matchKey(item.path) === requested);
  if (match) return match.path;

  match = blobs.find(item => {
    const itemPath = matchKey(item.path);
    return itemPath.endsWith(`/${requested}`) || requested.endsWith(`/${itemPath}`);
  });
  if (match) return match.path;

  const baseMatches = blobs.filter(item => pathBaseName(item.path) === requestedBase);
  return baseMatches.length === 1 ? baseMatches[0].path : '';
}

async function readGithubDefaultBranch(owner, repo) {
  try {
    const data = await fetchGithubJson(`https://api.github.com/repos/${owner}/${repo}`);
    return data.default_branch || 'main';
  } catch {
    return 'main';
  }
}

export async function fetchGithubSourceFiles(filePaths, repoUrl) {
  const repoInfo = parseGithubRepoUrl(repoUrl);
  if (!repoInfo) return { files: [], missing: filePaths || [], imported: 0, source: 'github' };

  const { owner, repo } = repoInfo;
  const branch = await readGithubDefaultBranch(owner, repo);
  let tree = [];

  try {
    const data = await fetchGithubJson(`https://api.github.com/repos/${owner}/${repo}/git/trees/${encodeURIComponent(branch)}?recursive=1`);
    tree = Array.isArray(data.tree) ? data.tree : [];
  } catch {
    tree = [];
  }

  const files = [];
  const missing = [];

  for (const requestedPath of filePaths || []) {
    const githubPath = findGithubTreePath(tree, requestedPath) || normalizePath(requestedPath);
    try {
      const rawUrl = `https://raw.githubusercontent.com/${owner}/${repo}/${encodeURIComponent(branch)}/${githubPath.split('/').map(encodeURIComponent).join('/')}`;
      const response = await fetch(rawUrl);
      if (!response.ok) throw new Error(`Raw GitHub source failed with HTTP ${response.status}.`);
      const source = await response.text();
      files.push({
        file_name: normalizePath(requestedPath),
        path: githubPath,
        source_code: source,
        source_mode: 'raw',
        origin: 'github_raw',
        repoUrl,
      });
    } catch {
      missing.push(requestedPath);
    }
  }

  return {
    files,
    missing,
    imported: files.length,
    source: 'github',
  };
}

function readUInt16(view, offset) {
  return view.getUint16(offset, true);
}

function readUInt32(view, offset) {
  return view.getUint32(offset, true);
}

function findEndOfCentralDirectory(view) {
  const minOffset = Math.max(0, view.byteLength - 65_557);
  for (let offset = view.byteLength - 22; offset >= minOffset; offset -= 1) {
    if (readUInt32(view, offset) === 0x06054b50) return offset;
  }
  return -1;
}

async function inflateRaw(bytes) {
  if (typeof DecompressionStream === 'undefined') {
    throw new Error('This browser cannot read compressed ZIP entries.');
  }
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

async function readZipEntry(view, bytes, entry) {
  const localOffset = entry.localHeaderOffset;
  if (readUInt32(view, localOffset) !== 0x04034b50) return null;

  const fileNameLength = readUInt16(view, localOffset + 26);
  const extraLength = readUInt16(view, localOffset + 28);
  const dataStart = localOffset + 30 + fileNameLength + extraLength;
  const compressed = bytes.slice(dataStart, dataStart + entry.compressedSize);

  if (entry.method === 0) return compressed;
  if (entry.method === 8) return inflateRaw(compressed);
  return null;
}

export async function readSourceFilesFromZip(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const endOffset = findEndOfCentralDirectory(view);
  if (endOffset < 0) throw new Error('ZIP central directory was not found.');

  const totalEntries = readUInt16(view, endOffset + 10);
  let centralOffset = readUInt32(view, endOffset + 16);
  const decoder = new TextDecoder('utf-8');
  const files = [];

  for (let index = 0; index < totalEntries; index += 1) {
    if (readUInt32(view, centralOffset) !== 0x02014b50) break;

    const method = readUInt16(view, centralOffset + 10);
    const compressedSize = readUInt32(view, centralOffset + 20);
    const uncompressedSize = readUInt32(view, centralOffset + 24);
    const fileNameLength = readUInt16(view, centralOffset + 28);
    const extraLength = readUInt16(view, centralOffset + 30);
    const commentLength = readUInt16(view, centralOffset + 32);
    const localHeaderOffset = readUInt32(view, centralOffset + 42);
    const nameStart = centralOffset + 46;
    const path = normalizePath(decoder.decode(bytes.slice(nameStart, nameStart + fileNameLength)));

    centralOffset = nameStart + fileNameLength + extraLength + commentLength;

    if (!path || path.endsWith('/') || !isSupportedSourcePath(path) || uncompressedSize > 500_000) continue;

    const entryBytes = await readZipEntry(view, bytes, { method, compressedSize, localHeaderOffset });
    if (!entryBytes) continue;

    files.push({
      path,
      source_code: decoder.decode(entryBytes),
      origin: 'uploaded_zip',
    });
  }

  return files;
}
