/**
 * Test helper utilities for FreightWatch integration tests
 */

/**
 * Generate a random UUID-like string for testing
 */
export function generateTestId(prefix = 'test'): string {
  return `${prefix}-${Math.random().toString(36).substr(2, 9)}`;
}

/**
 * Sleep for a given amount of time (useful for queue processing waits)
 */
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Create a mock Express.Multer.File object for testing
 */
export function createMockFile(
  filename: string,
  content: string | Buffer,
  mimetype = 'text/csv'
): Express.Multer.File {
  const buffer = typeof content === 'string' ? Buffer.from(content) : content;
  
  return {
    fieldname: 'file',
    originalname: filename,
    encoding: '7bit',
    mimetype,
    size: buffer.length,
    buffer,
    destination: '',
    filename: '',
    path: '',
    stream: null as any,
  };
}

/**
 * Wait for a condition to be true with timeout
 */
export async function waitForCondition(
  conditionFn: () => Promise<boolean>,
  timeoutMs = 5000,
  intervalMs = 100
): Promise<void> {
  const startTime = Date.now();
  
  while (Date.now() - startTime < timeoutMs) {
    if (await conditionFn()) {
      return;
    }
    await sleep(intervalMs);
  }
  
  throw new Error(`Condition not met within ${timeoutMs}ms timeout`);
}