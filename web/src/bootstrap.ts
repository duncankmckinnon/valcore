export async function startApp(
  render: () => void,
  initialize: () => Promise<unknown>,
  timeoutMs = 2_000,
): Promise<void> {
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      initialize(),
      new Promise<void>((resolve) => {
        timeout = setTimeout(resolve, timeoutMs);
      }),
    ]);
  } catch {
    // Frontend telemetry is optional and must never prevent the local UI from loading.
  } finally {
    if (timeout !== undefined) clearTimeout(timeout);
    render();
  }
}
