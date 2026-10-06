export type StreamEvent = { event: string; data: unknown };

function parseFrame(frame: string): StreamEvent | null {
  const lines = frame.split("\n");
  const event = lines.find((line) => line.startsWith("event: "))?.slice(7);
  const data = lines.filter((line) => line.startsWith("data: ")).map((line) => line.slice(6));
  if (!event || data.length === 0) return null;
  return { event, data: JSON.parse(data.join("\n")) };
}

export async function* readEvents(stream: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer = (buffer + decoder.decode(value, { stream: !done })).replaceAll("\r\n", "\n");
      if (buffer.length > 65536) throw new Error("Stream event too large");
      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const event = parseFrame(buffer.slice(0, boundary));
        if (event) yield event;
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}
