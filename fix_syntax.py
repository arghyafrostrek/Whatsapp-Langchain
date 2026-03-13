import codecs
import re

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\nodes.py", "r", "utf-8") as f:
    text = f.read()

# Replace broken f-string
text = re.sub(r'kb_text = f"\r?\nRelevant Knowledge:\r?\n\{rag_context\}\r?\n" if rag_context else ""',
              r'kb_text = f"\\nRelevant Knowledge:\\n{rag_context}\\n" if rag_context else ""', text)

# Replace broken sys_message
broken_sys = r'''sys_message_content = \(
                "You are an email composer for Frostrek LLP\.\r?\n"
                "Your ONLY job is to output a raw JSON object\. Nothing else\.\r?\n\r?\n"
                "\{\r?\n"
                '  "subject": "a short professional email subject line",\r?\n'
                '  "body": "a complete professional email body"\r?\n'
                "\}\r?\n\r?\n"
                "Rules:\r?\n"
                "- Always address the recipient politely\r?\n"
                "- Sign off every email as: Frosty \| AI Assistant, Frostrek LLP\r?\n"
                f"\{kb_text\}"
            \)'''

good_sys = '''sys_message_content = (
                "You are an email composer for Frostrek LLP.\\n"
                "Your ONLY job is to output a raw JSON object. Nothing else.\\n\\n"
                "{\\n"
                '  "subject": "a short professional email subject line",\\n'
                '  "body": "a complete professional email body"\\n'
                "}\\n\\n"
                "Rules:\\n"
                "- Always address the recipient politely\\n"
                "- Sign off every email as: Frosty | AI Assistant, Frostrek LLP\\n"
                f"{kb_text}"
            )'''

text = re.sub(broken_sys, good_sys, text, flags=re.MULTILINE)

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\nodes.py", "w", "utf-8") as f:
    f.write(text)
