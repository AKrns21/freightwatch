#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

/**
 * Recursively find all TypeScript files in a directory
 */
function findTsFiles(dir, fileList = []) {
  const files = fs.readdirSync(dir);

  files.forEach(file => {
    const filePath = path.join(dir, file);
    const stat = fs.statSync(filePath);

    if (stat.isDirectory()) {
      // Skip node_modules, dist, etc.
      if (!['node_modules', 'dist', 'build', '.git'].includes(file)) {
        findTsFiles(filePath, fileList);
      }
    } else if (file.endsWith('.ts')) {
      fileList.push(filePath);
    }
  });

  return fileList;
}

/**
 * Convert relative import to @/ alias
 */
function convertImport(importPath, currentFilePath, srcDir) {
  // Only process imports that go up directories
  if (!importPath.startsWith('..')) {
    return importPath;
  }

  // Get the directory containing the current file
  const currentDir = path.dirname(currentFilePath);

  // Resolve the absolute path of the import
  const absoluteImportPath = path.resolve(currentDir, importPath);

  // Get relative path from src directory
  const relativeFromSrc = path.relative(srcDir, absoluteImportPath);

  // Convert to @/ alias (normalize to forward slashes for consistency)
  const aliasPath = '@/' + relativeFromSrc.split(path.sep).join('/');

  return aliasPath;
}

/**
 * Process a single file
 */
function processFile(filePath, srcDir) {
  const content = fs.readFileSync(filePath, 'utf-8');
  let modified = false;
  let changeCount = 0;

  // Match import/export statements with relative paths going up directories
  const importRegex = /from\s+['"](\.\.[^'"]+)['"]/g;

  const newContent = content.replace(importRegex, (match, importPath) => {
    const newImportPath = convertImport(importPath, filePath, srcDir);

    if (newImportPath !== importPath) {
      modified = true;
      changeCount++;
      return `from '${newImportPath}'`;
    }

    return match;
  });

  if (modified) {
    fs.writeFileSync(filePath, newContent, 'utf-8');
    return changeCount;
  }

  return 0;
}

/**
 * Main execution
 */
function main() {
  const srcDir = path.resolve(__dirname, '../src');

  console.log('🔍 Finding TypeScript files...');
  const tsFiles = findTsFiles(srcDir);
  console.log(`📁 Found ${tsFiles.length} TypeScript files\n`);

  let totalFilesModified = 0;
  let totalImportsChanged = 0;
  const modifiedFiles = [];

  console.log('🔧 Processing files...');

  tsFiles.forEach(file => {
    const changes = processFile(file, srcDir);
    if (changes > 0) {
      totalFilesModified++;
      totalImportsChanged += changes;
      const relativePath = path.relative(process.cwd(), file);
      modifiedFiles.push({ path: relativePath, changes });
      console.log(`  ✓ ${relativePath} (${changes} import${changes > 1 ? 's' : ''})`);
    }
  });

  console.log('\n📊 Summary:');
  console.log(`  Files modified: ${totalFilesModified}`);
  console.log(`  Imports changed: ${totalImportsChanged}`);

  if (totalFilesModified > 0) {
    console.log('\n📝 Modified files:');
    modifiedFiles.forEach(({ path, changes }) => {
      console.log(`  - ${path} (${changes} change${changes > 1 ? 's' : ''})`);
    });
  }

  console.log('\n✅ Done!');
  console.log('💡 Next step: Run "npm run build" to verify compilation');
}

// Run the script
try {
  main();
} catch (error) {
  console.error('❌ Error:', error.message);
  console.error(error.stack);
  process.exit(1);
}
