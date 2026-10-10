/* eslint-disable @typescript-eslint/no-var-requires */
module.exports = {
  extends: ["@commitlint/config-conventional"],
  rules: {
    "type-enum": [
      2,
      "always",
      ["feat", "fix", "docs", "chore", "refactor", "perf", "test", "style", "revert", "build", "update", "wip"],
    ],
  },
};
