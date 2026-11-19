import { safeExternalUrl } from "../utils/site-config";
import { useState } from "react";
export default function ProductLogo({ software }) {
  const [failedUrl, setFailedUrl] = useState(null);
  const url = software.logo_url?.startsWith("/brands/") ? software.logo_url : safeExternalUrl(software.logo_url);
  return <div className="product-logo">{url && url !== failedUrl ? <img src={url} alt={`${software.name} 로고`} onError={() => setFailedUrl(url)} /> : <span aria-hidden="true">{software.name.slice(0, 2)}</span>}</div>;
}
