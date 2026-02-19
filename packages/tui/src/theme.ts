import type { EditorTheme, MarkdownTheme, SelectListTheme } from "@mariozechner/pi-tui";

const dim = (s: string) => `\x1b[2m${s}\x1b[22m`;
const bold = (s: string) => `\x1b[1m${s}\x1b[22m`;
const italic = (s: string) => `\x1b[3m${s}\x1b[23m`;
const underline = (s: string) => `\x1b[4m${s}\x1b[24m`;
const strikethrough = (s: string) => `\x1b[9m${s}\x1b[29m`;
const cyan = (s: string) => `\x1b[36m${s}\x1b[39m`;
const green = (s: string) => `\x1b[32m${s}\x1b[39m`;
const yellow = (s: string) => `\x1b[33m${s}\x1b[39m`;
const red = (s: string) => `\x1b[31m${s}\x1b[39m`;
const magenta = (s: string) => `\x1b[35m${s}\x1b[39m`;
const gray = (s: string) => `\x1b[90m${s}\x1b[39m`;
const white = (s: string) => `\x1b[37m${s}\x1b[39m`;
const bgGray = (s: string) => `\x1b[100m${s}\x1b[49m`;

const selectListTheme: SelectListTheme = {
  selectedPrefix: cyan,
  selectedText: white,
  description: gray,
  scrollInfo: gray,
  noMatch: gray,
};

export const editorTheme: EditorTheme = {
  borderColor: gray,
  selectList: selectListTheme,
};

export const markdownTheme: MarkdownTheme = {
  heading: bold,
  link: cyan,
  linkUrl: underline,
  code: yellow,
  codeBlock: white,
  codeBlockBorder: gray,
  quote: italic,
  quoteBorder: gray,
  hr: gray,
  listBullet: cyan,
  bold,
  italic,
  strikethrough,
  underline,
};

export const statusColor = gray;
export const errorColor = red;
export const userMsgColor = green;
