"""Java and C starter code for the sample coding problems, generated from the same input/output
contract as the Python starters (coding_specs.py).

Each starter = a stub the candidate fills in + I/O plumbing below it that they never edit.
Passing body=<reference code> builds the full reference program, which tests/test_starters.py
(and later the Piston verification) compile and run against every sample and hidden test.
"""
from __future__ import annotations

# ---------------------------------------------------------------- names and signatures
def camel(slug: str) -> str:
    head, *rest = slug.split("_")
    return head + "".join(w.capitalize() for w in rest)


JAVA_PARAMS = {"str": "String s", "two_str": "String a, String b", "int": "int n", "ints": "int[] nums",
               "ints_int": "int[] nums, int x", "two_ints": "int[] a, int[] b"}
CALL_ARGS = {"str": "s", "two_str": "a, b", "int": "n", "ints": "nums", "ints_int": "nums, x", "two_ints": "a, b"}
JAVA_RET = {"int": "int", "opt_int": "Integer", "bool": "boolean", "str": "String", "opt_char": "Character",
            "ints": "int[]", "pair_or_none": "int[]", "freq": "int[][]"}
JAVA_DEFAULT = {"int": "0", "opt_int": "null", "bool": "false", "str": "null", "opt_char": "null",
                "ints": "null", "pair_or_none": "null", "freq": "null"}
JAVA_HINT = {"int": "an int", "opt_int": "an Integer, or null if there is no answer", "bool": "true or false",
             "str": "a String", "opt_char": "a Character, or null if there is none", "ints": "an int[]",
             "pair_or_none": "an int[] {i, j}, or null if there is none",
             "freq": "an int[][] of {value, count} pairs sorted by value"}

JAVA_PARSE = {
    "str": 'String s = in.readLine();\n        if (s == null) s = "";',
    "two_str": 'String a = in.readLine();\n        String b = in.readLine();\n        if (a == null) a = "";\n        if (b == null) b = "";',
    "int": "int n = Integer.parseInt(in.readLine().trim());",
    "ints": "int k = Integer.parseInt(in.readLine().trim());\n        int[] nums = readInts(in);",
    "ints_int": "int k = Integer.parseInt(in.readLine().trim());\n        int[] nums = readInts(in);\n        int x = Integer.parseInt(in.readLine().trim());",
    "two_ints": "int k = Integer.parseInt(in.readLine().trim());\n        int[] a = readInts(in);\n"
                "        int m = Integer.parseInt(in.readLine().trim());\n        int[] b = readInts(in);",
}
JAVA_PRINT = {
    "int": "System.out.println(result);",
    "opt_int": 'System.out.println(result == null ? "NONE" : result.toString());',
    "bool": 'System.out.println(result ? "TRUE" : "FALSE");',
    "str": "System.out.println(result);",
    "opt_char": 'System.out.println(result == null ? "NONE" : String.valueOf(result));',
    "ints": "StringBuilder sb = new StringBuilder();\n        for (int i = 0; i < result.length; i++) {\n"
            '            if (i > 0) sb.append(\' \');\n            sb.append(result[i]);\n        }\n        System.out.println(sb);',
    "pair_or_none": 'System.out.println(result == null ? "NONE" : result[0] + " " + result[1]);',
    "freq": 'for (int[] row : result) System.out.println(row[0] + " " + row[1]);',
}


def java_starter(spec: dict, body: str | None = None) -> str:
    name, inp, out = camel(spec["slug"]), spec["inp"], spec["out"]
    ret = JAVA_RET[out]
    stub = body or f"        // Return {JAVA_HINT[out]}.\n        return {JAVA_DEFAULT[out]};"
    return (
        "import java.io.*;\nimport java.util.*;\n\npublic class Main {\n"
        f"    static {ret} {name}({JAVA_PARAMS[inp]}) {{\n{stub}\n    }}\n\n"
        "    // ---- input/output plumbing: you do not need to change anything below ----\n"
        "    public static void main(String[] args) throws IOException {\n"
        "        BufferedReader in = new BufferedReader(new InputStreamReader(System.in));\n"
        f"        {JAVA_PARSE[inp]}\n        {ret} result = {name}({CALL_ARGS[inp]});\n"
        f"        {JAVA_PRINT[out]}\n    }}\n\n" + JAVA_READ_INTS + "}\n"
    )


JAVA_READ_INTS = (
    "    static int[] readInts(BufferedReader in) throws IOException {\n"
    "        String line = in.readLine();\n"
    "        if (line == null || line.trim().isEmpty()) return new int[0];\n"
    "        StringTokenizer st = new StringTokenizer(line);\n"
    "        int[] out = new int[st.countTokens()];\n"
    "        for (int i = 0; i < out.length; i++) out[i] = Integer.parseInt(st.nextToken());\n"
    "        return out;\n    }\n"
)

# ---------------------------------------------------------------- C
C_PARAMS = {"str": "const char *s", "two_str": "const char *a, const char *b", "int": "int n",
            "ints": "const int *nums, int k", "ints_int": "const int *nums, int k, int x",
            "two_ints": "const int *a, int k, const int *b, int m"}
C_CALL = {"str": "s", "two_str": "a, b", "int": "n", "ints": "nums, k", "ints_int": "nums, k, x", "two_ints": "a, k, b, m"}
C_PARSE = {
    "str": "char *s = read_line();",
    "two_str": "char *a = read_line();\n    char *b = read_line();",
    "int": "int n = atoi(read_line());",
    "ints": "int k = atoi(read_line());\n    int *nums = read_ints(k);",
    "ints_int": "int k = atoi(read_line());\n    int *nums = read_ints(k);\n    int x = atoi(read_line());",
    "two_ints": "int k = atoi(read_line());\n    int *a = read_ints(k);\n    int m = atoi(read_line());\n    int *b = read_ints(m);",
}
C_CAP = {"ints": "k + 1", "ints_int": "k + 1", "two_ints": "k + m + 1"}
# (extra parameters, return type, default return line, hint)
C_OUT = {
    "int": ("", "int", "return 0;", "Return the int."),
    "opt_int": (", int *result", "int", "return 0;", "Write the answer to *result and return 1, or return 0 if there is no answer."),
    "bool": ("", "int", "return 0;", "Return 1 for TRUE, 0 for FALSE."),
    "str": (", char *out", "void", "", "Write the resulting string into out (it has room for the whole string and a '\\0')."),
    "opt_char": (", char *result", "int", "return 0;", "Write the character to *result and return 1, or return 0 if there is none."),
    "ints": (", int *out", "int", "return 0;", "Write the integers into out (it has enough room) and return how many you wrote."),
    "pair_or_none": (", int *i, int *j", "int", "return 0;", "Write the positions (i < j) and return 1, or return 0 if there is none."),
    "freq": (", int *values, int *counts", "int", "return 0;", "Write each distinct value (ascending) into values and its count into counts; return how many distinct values."),
}
C_RUN = {
    "int": "int result = {f}({args});\n    printf(\"%d\\n\", result);",
    "opt_int": "int value;\n    if ({f}({args}, &value)) printf(\"%d\\n\", value);\n    else printf(\"NONE\\n\");",
    "bool": "printf(\"%s\\n\", {f}({args}) ? \"TRUE\" : \"FALSE\");",
    "str": "char *out = malloc(strlen(s) + 1);\n    {f}({args}, out);\n    printf(\"%s\\n\", out);",
    "opt_char": "char c;\n    if ({f}({args}, &c)) printf(\"%c\\n\", c);\n    else printf(\"NONE\\n\");",
    "ints": "int *out = malloc(sizeof(int) * ({cap}));\n    int count = {f}({args}, out);\n"
            "    for (int idx = 0; idx < count; idx++) printf(idx ? \" %d\" : \"%d\", out[idx]);\n    printf(\"\\n\");",
    "pair_or_none": "int i, j;\n    if ({f}({args}, &i, &j)) printf(\"%d %d\\n\", i, j);\n    else printf(\"NONE\\n\");",
    "freq": "int *values = malloc(sizeof(int) * (k + 1));\n    int *counts = malloc(sizeof(int) * (k + 1));\n"
            "    int distinct = {f}({args}, values, counts);\n    for (int idx = 0; idx < distinct; idx++) printf(\"%d %d\\n\", values[idx], counts[idx]);",
}
C_PLUMBING = r"""
/* ---- input/output plumbing: you do not need to change anything below ---- */
static char *read_line(void) {
    size_t cap = 256, len = 0;
    char *buf = malloc(cap);
    int ch;
    while ((ch = fgetc(stdin)) != EOF && ch != '\n') {
        if (len + 2 > cap) { cap *= 2; buf = realloc(buf, cap); }
        buf[len++] = (char)ch;
    }
    while (len > 0 && buf[len - 1] == '\r') len--;
    buf[len] = '\0';
    return buf;
}

static int *read_ints(int count) {
    int *out = malloc(sizeof(int) * (count + 1));
    char *line = read_line(), *p = line;
    for (int i = 0; i < count; i++) out[i] = (int)strtol(p, &p, 10);
    free(line);
    return out;
}
"""


def c_starter(spec: dict, body: str | None = None) -> str:
    f, inp, out = spec["slug"], spec["inp"], spec["out"]
    extra, ret, default, hint = C_OUT[out]
    stub = body or f"    /* {hint} */\n    {default}".rstrip()
    run = C_RUN[out].format(f=f, args=C_CALL[inp], cap=C_CAP.get(inp, ""))
    return (
        "#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\n\n"
        f"{ret} {f}({C_PARAMS[inp]}{extra}) {{\n{stub}\n}}\n" + C_PLUMBING + "\n"
        f"int main(void) {{\n    {C_PARSE[inp]}\n    {run}\n    return 0;\n}}\n"
    )


# ---------------------------------------------------------------- reference bodies (used only to verify the starters)
REF_JAVA = {
    "sum_of_evens": "        int total = 0;\n        for (int v : nums) if (v % 2 == 0) total += v;\n        return total;",
    "longest_run": "        int best = 0, run = 0;\n        for (int i = 0; i < nums.length; i++) {\n"
                   "            run = (i > 0 && nums[i] == nums[i - 1]) ? run + 1 : 1;\n            best = Math.max(best, run);\n        }\n        return best;",
}

REF_C = {
    "sum_of_evens": "    int total = 0;\n    for (int i = 0; i < k; i++) if (nums[i] % 2 == 0) total += nums[i];\n    return total;",
    "longest_run": "    int best = 0, run = 0;\n    for (int i = 0; i < k; i++) {\n        run = (i > 0 && nums[i] == nums[i - 1]) ? run + 1 : 1;\n"
                   "        if (run > best) best = run;\n    }\n    return best;",
}
