import en from "@/i18n/messages/en.json";

type Messages = typeof en;

const messagesMap: Record<string, Messages> = { en };

export function getTranslations(locale: string, namespace: string) {
  const messages = messagesMap[locale] || en;
  const ns = (messages as Record<string, Record<string, string>>)[namespace];
  const fallbackNs = (en as Record<string, Record<string, string>>)[namespace];
  return (key: string): string => {
    return ns?.[key] || fallbackNs?.[key] || key;
  };
}
