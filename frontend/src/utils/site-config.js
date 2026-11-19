export function safeExternalUrl(value) {
  if (typeof value !== "string" || !/^https:\/\//i.test(value) || /[\s\\]/.test(value)) return undefined;
  try {
    const url = new URL(value);
    return url.hostname && !url.username && !url.password ? url.href : undefined;
  } catch {
    return undefined;
  }
}

export const publicDeployment = import.meta.env.VITE_PUBLIC_DEPLOYMENT === "true";
export const repositoryUrl = safeExternalUrl(import.meta.env.VITE_REPOSITORY_URL);
export const operatorName = import.meta.env.VITE_OPERATOR_NAME?.trim() || "딜나루 운영자";
export const supportEmail = /^[^\s@<>?:]+@[^\s@<>?:]+\.[^\s@<>?:]+$/.test(import.meta.env.VITE_SUPPORT_EMAIL || "")
  ? import.meta.env.VITE_SUPPORT_EMAIL
  : "";
export const hostingNotice = import.meta.env.VITE_HOSTING_NOTICE?.trim();
export const mailNotice = import.meta.env.VITE_MAIL_NOTICE?.trim();
export const retentionNotice = import.meta.env.VITE_RETENTION_NOTICE?.trim();
export const privacyOfficer = import.meta.env.VITE_PRIVACY_OFFICER?.trim() || operatorName;
export const overseasNotice = import.meta.env.VITE_OVERSEAS_NOTICE?.trim();
