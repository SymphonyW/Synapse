import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'

export function ChatMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        a: ({ href, children }) => {
          const normalizedHref = typeof href === 'string' ? href.trim() : ''
          if (normalizedHref === '') {
            return <span>{children}</span>
          }

          return (
            <a href={normalizedHref} rel="noreferrer noopener" target="_blank">
              {children}
            </a>
          )
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}
