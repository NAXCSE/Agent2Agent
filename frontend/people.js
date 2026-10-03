// Input validation for the people form, kept separate so it can be tested
// without a browser. Loaded before app.js.

const MIN_PEOPLE = 2;

/**
 * Validate raw form entries into people ready for POST /api/people/analyze.
 *
 * @param {Array<{linkedin: string, instagram: string}>} entries
 * @returns {{complete: boolean, missing: string[], people: Array<{linkedin_url: string, instagram_url: string}>}}
 */
function collectPeople(entries) {
  const people = [];
  const missing = [];

  entries.forEach((entry, position) => {
    const linkedin = (entry.linkedin || "").trim();
    const instagram = (entry.instagram || "").trim();

    // A completely blank row is just an unused extra slot.
    if (!linkedin && !instagram) return;

    if (!linkedin || !instagram) {
      missing.push(
        `Person ${position + 1} needs both a LinkedIn and an Instagram URL`
      );
      return;
    }

    people.push({ linkedin_url: linkedin, instagram_url: instagram });
  });

  return {
    complete: people.length >= MIN_PEOPLE && missing.length === 0,
    missing,
    people,
  };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { MIN_PEOPLE, collectPeople };
}