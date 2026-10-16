#include "HtmlUtilities.h"

QString HtmlUtilities::mdToHtml(QString txt) {
    // Escape HTML first? No, the original content might contain HTML.
    // We will only escape inside code blocks to be safe.
    
    // 1. Code blocks with optional language and syntax highlighting
    QRegularExpression re("```(\\w*)\\n?(.*?)\\n?```", QRegularExpression::DotMatchesEverythingOption);
    int offset = 0;
    QRegularExpressionMatch match;
    while ((match = re.match(txt, offset)).hasMatch()) {
        QString lang = match.captured(1);
        QString code = match.captured(2);
        
        // Escape HTML
        code = code.toHtmlEscaped();
        
        // Basic Syntax Highlighting (Dark+ Theme)
        // Strings (orange)
        code.replace(QRegularExpression("(&quot;.*?&quot;)"), "<span style='color:#CE9178;'>\\1</span>");
        
        // Keywords (blue)
        code.replace(QRegularExpression("\\b(int|float|double|bool|void|char|class|struct|if|else|for|while|return|new|delete|try|catch|const|static|auto|std|public|private|protected|virtual|override|namespace|using|true|false)\\b"), 
                     "<span style='color:#569CD6;'>\\1</span>");
                     
        // Comments (green) - non-greedy match to newline
        code.replace(QRegularExpression("(//.*?)(?:\\n|$)"), "<span style='color:#6A9955;'>\\1</span>\n");
        
        // Classes/Types starting with capital letter (green-blue)
        code.replace(QRegularExpression("\\b([A-Z][a-zA-Z0-9_]*)\\b"), "<span style='color:#4EC9B0;'>\\1</span>");

        // Functions (yellow) - match word followed by (
        code.replace(QRegularExpression("\\b([a-z_][a-zA-Z0-9_]*)\\s*\\("), "<span style='color:#DCDCAA;'>\\1</span>(");

        // Preprocessor (purple)
        code.replace(QRegularExpression("(#include|#define|#if|#endif|#pragma)"), "<span style='color:#C586C0;'>\\1</span>");

        QString highlightedBlock = 
            "<div style='background:#0d0d0d; border:1px solid #2e2e3a; border-radius:8px; margin:10px 0;'>"
            "<div style='background:#1a1a1f; color:#888; font-size:10px; padding:3px 10px; border-bottom:1px solid #2e2e3a; text-transform:uppercase;'>" + (lang.isEmpty() ? "code" : lang) + "</div>"
            "<pre style='color:#d1d1d1; padding:10px; margin:0; font-family:\"Consolas\", \"Monaco\", monospace; font-size:13px;'>" + code + "</pre>"
            "</div>";
            
        txt.replace(match.capturedStart(), match.capturedLength(), highlightedBlock);
        offset = match.capturedStart() + highlightedBlock.length();
    }
    
    // 2. Inline code
    txt.replace(QRegularExpression("`(.*?)`"), "<code style='background:#2d2d35; color:#e6e6e6; padding:1px 4px; border-radius:4px; font-family:monospace;'>\\1</code>");

    // 3. Source Citations [Source: path] and [Tai lieu: path]
    txt.replace(QRegularExpression("\\[Source:\\s*(.*?)\\]"), 
                "<a href='file:///\\1' class='source-citation'>📄 \\1</a>");
    txt.replace(QRegularExpression("\\[Tai lieu:\\s*(.*?)\\]"), 
                "<a href='file:///\\1' class='source-citation doc-citation'>📁 \\1</a>");

    // 4. Headers
    txt.replace(QRegularExpression("(?m)^### (.*)"), "<h3>\\1</h3>");
    txt.replace(QRegularExpression("(?m)^## (.*)"), "<h2>\\1</h2>");
    txt.replace(QRegularExpression("(?m)^# (.*)"), "<h1>\\1</h1>");

    // 5. Bold and Italic
    txt.replace(QRegularExpression("\\*\\*(.*?)\\*\\*"), "<b>\\1</b>");
    txt.replace(QRegularExpression("\\*(.*?)\\*"), "<i>\\1</i>");

    // 6. Lists (Handle simple bullet points)
    // Wrap entire lists in <ul>
    QStringList lines = txt.split("\n");
    bool inList = false;
    for (int i = 0; i < lines.size(); ++i) {
        if (lines[i].trimmed().startsWith("- ") || lines[i].trimmed().startsWith("* ")) {
            QString content = lines[i].trimmed().mid(2);
            lines[i] = "<li>" + content + "</li>";
            if (!inList) {
                lines[i] = "<ul>" + lines[i];
                inList = true;
            }
        } else {
            if (inList) {
                lines[i-1] += "</ul>";
                inList = false;
            }
        }
    }
    if (inList) lines.last() += "</ul>";
    txt = lines.join("\n");

    // 7. Newlines
    txt.replace("\n", "<br>");
    
    return txt;
}
