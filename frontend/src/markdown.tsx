import type { ReactNode } from "react";

const ORDERED_ITEM = /^(\d+)\.\s+(.*)$/;
const UNORDERED_ITEM = /^[-*]\s+(.*)$/;

// Parser de markdown minimo para las respuestas del asistente: negrillas,
// listas (numeradas y con vinetas) y parrafos separados por linea en blanco.
// No usamos una libreria de markdown completa porque el LLM solo produce
// este subconjunto y evita sumar una dependencia para tan poco.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g).filter((part) => part !== "");
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    return <span key={`${keyPrefix}-${index}`}>{part}</span>;
  });
}

export function renderMessageContent(content: string): ReactNode {
  const lines = content.split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];
  let listType: "ol" | "ul" | null = null;
  let paragraphLines: string[] = [];

  function flushList() {
    if (listItems.length === 0) return;
    const ListTag = listType === "ol" ? "ol" : "ul";
    blocks.push(
      <ListTag key={`list-${blocks.length}`} className="chat-msg__list">
        {listItems.map((item, index) => (
          <li key={index}>{renderInline(item, `li-${blocks.length}-${index}`)}</li>
        ))}
      </ListTag>,
    );
    listItems = [];
    listType = null;
  }

  function flushParagraph() {
    if (paragraphLines.length === 0) return;
    blocks.push(
      <p key={`p-${blocks.length}`} className="chat-msg__p">
        {paragraphLines.map((line, index) => (
          <span key={index}>
            {renderInline(line, `pl-${blocks.length}-${index}`)}
            {index < paragraphLines.length - 1 && <br />}
          </span>
        ))}
      </p>,
    );
    paragraphLines = [];
  }

  for (const rawLine of lines) {
    const line = rawLine.trim();

    if (line === "") {
      flushParagraph();
      flushList();
      continue;
    }

    const orderedMatch = line.match(ORDERED_ITEM);
    const unorderedMatch = line.match(UNORDERED_ITEM);

    if (orderedMatch) {
      flushParagraph();
      if (listType !== "ol") flushList();
      listType = "ol";
      listItems.push(orderedMatch[2]);
      continue;
    }

    if (unorderedMatch) {
      flushParagraph();
      if (listType !== "ul") flushList();
      listType = "ul";
      listItems.push(unorderedMatch[1]);
      continue;
    }

    flushList();
    paragraphLines.push(line);
  }

  flushParagraph();
  flushList();

  return blocks.length > 0 ? blocks : content;
}
