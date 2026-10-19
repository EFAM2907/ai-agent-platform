import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Respuestas del asistente como markdown real (listas, tablas, código,
// enlaces). react-markdown NO interpreta HTML crudo del texto -- lo que
// el modelo (o un dato que el modelo repite) escriba como <script> se
// muestra como texto, nunca se ejecuta -- y los enlaces se abren en una
// pestaña nueva sin darle acceso a esta ventana (noopener).
export function renderMessageContent(content: string): ReactNode {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // Las tablas anchas hacen scroll dentro del mensaje en vez de
          // ensanchar toda la página.
          table: ({ children }) => (
            <div className="md__table-wrap">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
