GEMINI_PROMPT = """Objective:
Convert input documents (PDF/Image) from Netaji Subhas University of Technology notices into ACCURATE, well-structured Markdown and then into context-rich JSON chunks optimized for high-quality RAG retrieval. The output must always be complete and valid JSON, containing all required information. Do not omit any content or leave behind any markdown formatting. Your output must adhere exactly to the format below, and if your output is truncated or invalid you will be given your incorrect attempt, you must reattempt using a different approach until the complete JSON is produced correctly.

📄 Final JSON Output Format:
json
{
  "chunks": [
    {
      "text": "Markdown-formatted content preserving original structure (headings, paragraphs, lists, tables)",
      "optional_summary": "Concise, retrieval-optimized summary explicitly highlighting key details (only for tabular data; "null" otherwise)"
    },
    ...
  ],
  "summary": "Concise page-level summary optimized explicitly for retrieval (highlighting key details like exam names, dates, roll numbers, courses, semesters)"
}

🔍 Step-by-Step Processing Guidelines:

1️⃣ OCR & Markdown Conversion (for images/PDFs):
 -Extract text accurately using OCR if input is an image.
 -Correct OCR inconsistencies before Markdown conversion.
 -Preserve original formatting rigorously:
   -Headings: Convert to Markdown (# H1, ## H2, ### H3) maintaining hierarchy.
   -Paragraphs & Line Breaks: Retain exact spacing; do not merge paragraphs.
   -Lists: Convert bullets (- /-/) to Markdown (-/), numbered lists as-is (1., 2.).
   -Bold/Italics:
     -Bold → **bold**
     -Italic → *italic*
     -Bold & Italic → ***bold and italic***

2️⃣ Advanced Table Formatting Rules:
 -Convert tables strictly into Markdown (| Column | Column |), preserving exact headers and alignments.
 -Pay very close attention to complex table structures of multi-row data
 -Clearly restate table titles or section headings if splitting tables across multiple chunks.
 -Never split rows mid-way; split only at row boundaries.
 -each table chunk should strive to preserve semantic/interrelated context together as single chunk(like small schedule tables, timetables, teacher subject mapping, etc). IF and ONLY IF context is SEPERATE and not related to each other(like results, course committees, etc) a minimum of 3 and maximum of 5 rows should be in a chunk.
 -always add seperation between table rows and headers with |-----------|-------------|--------|
 -Always re-add a table title and column headings if splitting tables across multiple chunks.
 -intelligently check for correct markdown formmatting of tables

✅ Correct Example of Table Splitting:
### Exam Schedule
| Date      | Subject     | Time   |
|-----------|-------------|--------|
| March 14  | Physics     | 9:00AM |
| March 15  | Mathematics | 9:00AM |

### Exam Schedule (Continued)
| Date      | Subject     | Time   |
|-----------|-------------|--------|
| March 16  | Chemistry   | 9:00AM |


❌ Incorrect Example:
Splitting rows mid-way or omitting table headers/context.
| Date       | Subject  
|------------|------------
| March 15  | Mathematics
NEVER OUTPUT A TABLE LIKE THIS (STRICT)

📌 Context-Aware Chunking Strategy:

1️⃣ Chunk Size & Overlap:
 -Aim for ~500-600 tokens per chunk.
 -Maintain at least a 100-token overlap (copy text) between adjacent chunks to ensure semantic continuity. Increase overlap if necessary for better context retention.
 -Do not create very small chunks if a chunk is just 1 or 2 lines, include it in it's upper or lower chunk
 -No chunk should be meaningless or context-less on its own
 -DO NOT add Netaji Subhas University of Technology generic headers into a chunk, example (# NETAJI SUBHAS UNIVERSITY OF TECHNOLOGY\n(formerly Netaji Subhas Institute of Technology)\nAzad Hind Fauj Marg, Sector-3, Dwarka, New Delhi-110078\nPhone No. 25000268 Extn.-2325, Fax: 25099025\nWebsite: www.nsut.ac.in), ignore ay such headers that are only talking about nsut and not anything about the document itself

2️⃣ Semantic Integrity & Contextual Independence:
 -Each chunk must be fully understandable in isolation:
 -Clearly restate headings or titles if content spans multiple chunks/pages.
 -Avoid vague references ("see next page," "continued below").
 -Ensure each chunk contains meaningful information that can independently answer potential user queries.
 -Ensure context flows correctly and each chunk is meaningful using the previous page summary, You may add details yourself to "text" when tables are not there and to "optional_summary" when tables are there

📝 Enhanced Summary Generation Rules:

1️⃣ When to Summarize:
 -Generate summaries exclusively for chunks containing tabular data benefiting from structured summarization.
 -Set optional_summary explicitly to null for purely text-based chunks.
 -optional_summary should be very specific and must include all entitites by name mentioned in it, do not generalize it

2️⃣ Summary Quality Standards:
✅ Include explicit key details clearly
❌ Avoid vague phrases:
"etc."
"various students"
"more data available"
✅ generate page summaries in a way that next page will have sufficient information about the context while correctly representing current page

✅ Correct Optional Summary Example:
"optional_summary": "B.Tech Electronics & Communication Engineering (Internet of Things), Semester 5 (Nov-Dec 2024) results for Madhur Choudhary (Roll No. 2022UEI2837) and Kushagra Kumar (Roll No. 2022UEI2838)."

❌ Incorrect Optional Summary Example(too vague):
"optional_summary": "Exam results for roll numbers listed."
"optional_summary": "contains results for B.Tech Electronics and Communication Engineering - Internet of Things, 5th Semester (Nov-Dec 2024) for roll number 100 to 111"

📑 Page-Level Retrieval Optimized Summary:
Provide a concise yet detailed summary at the page level explicitly highlighting essential information such as:
Exam/course details
Key dates/deadlines
Important roll number ranges or student names
etc.

✅ Correct Page-Level Summary Example:
"summary": "This page contains B.Tech Electronics & Communication Engineering Semester 5 results (Nov-Dec 2024) for roll numbers from 2022UEI2837 to 2022UEI2865 and important notices regarding project submission deadlines."

❌ Incorrect Page-Level Summary Example(too vague):
"summary": "This page contains some results for B.Tech Electronics and Communication Engineering - Internet of Things, 5th Semester (Nov-Dec 2024)"
🧑‍💻 Final JSON Output Example:
{
  "chunks": [
    {
      "text": "### B.Tech Electronics & Communication Engineering\n### Semester 5 Exam Results (Nov-Dec 2024)\n\n| Sl | Roll No | Name             | SGPA |\n|----|---------|------------------|------|\n|37  |2022UEI2837| MADHUR CHOUDHARY |6.43 |\n|38  |2022UEI2838| KUSHAGRA KUMAR   |7.57 |\n",
      "optional_summary": "B.Tech Electronics & Communication Engineering Semester 5 (Nov-Dec 2024) results for Madhur Choudhary (Roll No.2022UEI2837) and Kushagra Kumar (Roll No.2022UEI2838)."
    },
    {
      "text": "### Important Notice\n\nAll students must submit their project reports by **March10,2025**. Late submissions will incur penalties.\n\nFor queries, contact the academic office.",
      "optional_summary": "null"
    }
  ],
  
  "summary": "This page contains B.Tech Electronics & Communication Engineering Semester5 exam results(Nov-Dec2024)for roll numbers from 2022UEI2837 to 2022UEI2865 and a notice regarding the project submission deadline(March10,2025)."
}

If your output gets truncated or is invalid, you will be provided with an example of the truncated output and the corresponding image for that page. In that case, use an alternative method to regenerate the complete JSON output without truncation. Ensure the final output is valid JSON and follows the exact format above."""