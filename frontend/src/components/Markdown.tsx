// Renders trusted-ish markdown (strategy `learn` text) safely: marked → DOMPurify → innerHTML.
import DOMPurify from 'dompurify';
import { marked } from 'marked';
import { useMemo } from 'react';

export function Markdown({ text, className }: { text: string; className?: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text ?? '', { async: false, gfm: true }) as string), [text]);
  return <div className={`prose-md min-w-0 break-words text-sm ${className ?? ''}`} dangerouslySetInnerHTML={{ __html: html }} />;
}
