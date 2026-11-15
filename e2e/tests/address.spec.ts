// The address rule (#234). No browser is used here: these run wherever the Playwright runner does.
import { expect, test } from "@playwright/test";
import { baseUrl, checkedUrl, required } from "../lib/stack";

const NAME = "SOCBOARD_BASE_URL";

for (const value of [
  "http://127.0.0.1:8080",
  "http://localhost:8080/",
  "http://[::1]:8080",
  "http://127.0.0.1",
  "HTTP://LOCALHOST:8080",
]) {
  test(`a loopback http address is accepted: ${value}`, () => {
    expect(checkedUrl(NAME, value)).toBe(value.replace(/\/$/, ""));
  });
}

for (const value of [
  "https://127.0.0.1:8080",
  "http://example.com",
  "http://10.0.0.5:8080",
  "http://0.0.0.0:8080",
  "http://user:secret@127.0.0.1:8080",
  "http://127.0.0.1:8080/api",
  "http://127.0.0.1:8080?x=1",
  "http://127.0.0.1:8080#top",
  "http://127.0.0.1:99999",
  "http://127.0.0.1:0",
  "http://127.0.0.1:port",
  " http://127.0.0.1:8080",
  "http://127.0.0.1:8080\n",
  "http://localhost.:8080",
  "http://[::ffff:127.0.0.1]:8080",
  "http://127.0.0.1@evil.example:8080",
  "http://evil.example@127.0.0.1:8080",
  "127.0.0.1:8080",
]) {
  test(`any other address is refused without printing it: ${JSON.stringify(value)}`, () => {
    let message = "";
    try {
      checkedUrl(NAME, value);
    } catch (error) {
      message = (error as Error).message;
    }
    expect(message).toContain(NAME);
    expect(message).not.toContain(value.trim());
  });
}

test("an unset or empty address means no stack", () => {
  expect(baseUrl({})).toBeUndefined();
  expect(baseUrl({ [NAME]: "" })).toBeUndefined();
});

test("a missing variable fails naming it and never printing a value", () => {
  for (const environ of [{}, { SOCBOARD_INGEST_API_KEY: "" }, { SOCBOARD_INGEST_API_KEY: "   " }]) {
    expect(() => required("SOCBOARD_INGEST_API_KEY", environ)).toThrow("SOCBOARD_INGEST_API_KEY");
  }
});
