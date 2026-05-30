export function parsePythonLiteral(value: string | undefined): unknown {
  const parser = new PythonLiteralParser(value ?? "");
  return parser.parse();
}

class PythonLiteralParser {
  private index = 0;

  constructor(private readonly input: string) {}

  parse() {
    this.skipWhitespace();
    const value = this.parseValue();
    this.skipWhitespace();

    if (this.index !== this.input.length) {
      throw new Error(`Unexpected token at offset ${this.index}`);
    }

    return value;
  }

  private parseValue(): unknown {
    this.skipWhitespace();
    const char = this.peek();

    if (char === "{") {
      return this.parseObject();
    }

    if (char === "[") {
      return this.parseArray();
    }

    if (char === "'" || char === '"') {
      return this.parseString();
    }

    if (char === "-" || isDigit(char)) {
      return this.parseNumber();
    }

    return this.parseIdentifier();
  }

  private parseObject(): Record<string, unknown> {
    const object: Record<string, unknown> = {};
    this.expect("{");
    this.skipWhitespace();

    if (this.peek() === "}") {
      this.index += 1;
      return object;
    }

    while (this.index < this.input.length) {
      const key = this.parseValue();

      if (typeof key !== "string") {
        throw new Error(`Object key must be a string at offset ${this.index}`);
      }

      this.skipWhitespace();
      this.expect(":");
      object[key] = this.parseValue();
      this.skipWhitespace();

      if (this.peek() === "}") {
        this.index += 1;
        return object;
      }

      this.expect(",");
      this.skipWhitespace();
    }

    throw new Error("Unterminated object literal");
  }

  private parseArray(): unknown[] {
    const array: unknown[] = [];
    this.expect("[");
    this.skipWhitespace();

    if (this.peek() === "]") {
      this.index += 1;
      return array;
    }

    while (this.index < this.input.length) {
      array.push(this.parseValue());
      this.skipWhitespace();

      if (this.peek() === "]") {
        this.index += 1;
        return array;
      }

      this.expect(",");
      this.skipWhitespace();
    }

    throw new Error("Unterminated array literal");
  }

  private parseString() {
    const quote = this.peek();
    this.index += 1;
    let result = "";

    while (this.index < this.input.length) {
      const char = this.input[this.index];
      this.index += 1;

      if (char === quote) {
        return result;
      }

      if (char === "\\") {
        const escaped = this.input[this.index];
        this.index += 1;
        result += escaped ?? "";
        continue;
      }

      result += char;
    }

    throw new Error("Unterminated string literal");
  }

  private parseNumber() {
    const start = this.index;

    if (this.peek() === "-") {
      this.index += 1;
    }

    while (isDigit(this.peek())) {
      this.index += 1;
    }

    if (this.peek() === ".") {
      this.index += 1;

      while (isDigit(this.peek())) {
        this.index += 1;
      }
    }

    const parsed = Number(this.input.slice(start, this.index));

    if (!Number.isFinite(parsed)) {
      throw new Error(`Invalid number at offset ${start}`);
    }

    return parsed;
  }

  private parseIdentifier() {
    const start = this.index;

    while (/[A-Za-z_]/.test(this.peek())) {
      this.index += 1;
    }

    const identifier = this.input.slice(start, this.index);

    if (identifier === "True") {
      return true;
    }

    if (identifier === "False") {
      return false;
    }

    if (identifier === "None") {
      return null;
    }

    throw new Error(`Unknown identifier "${identifier}" at offset ${start}`);
  }

  private skipWhitespace() {
    while (/\s/.test(this.peek())) {
      this.index += 1;
    }
  }

  private expect(expected: string) {
    if (this.peek() !== expected) {
      throw new Error(
        `Expected "${expected}" at offset ${this.index}, got "${this.peek()}"`
      );
    }

    this.index += 1;
  }

  private peek() {
    return this.input[this.index] ?? "";
  }
}

function isDigit(value: string) {
  return value >= "0" && value <= "9";
}
