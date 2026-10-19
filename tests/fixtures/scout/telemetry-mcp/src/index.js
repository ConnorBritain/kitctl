// Usage statistics are collected by default; pass --no-usage-statistics to opt out.
import { PostHog } from 'posthog-node';
const client = new PostHog('phc_fixture', { host: 'https://eu.posthog.com' });
export async function report(event) {
  await fetch('https://api.telemetry.example/v1/events', { method: 'POST', body: JSON.stringify(event) });
}
