/**
 * Пошаговые инструкции по подключению.
 *
 * Контент зеркалирует шаги Telegram-бота (функции win_step / android_step /
 * ios_step / mac_step / tv_step в backend/src/app/bot_transport/storefront_ui.py),
 * адаптирован под веб. Ссылки на магазины приложений и GitHub взяты из бота
 * (проверенные). Ссылка подписки пользователем копируется в Личном кабинете → Ключи.
 */

export type InstructionLink = { href: string; label: string };

export type InstructionStep = {
  title: string;
  description: string;
  /** Кнопки-ссылки для шага (скачать приложение и т.п.). */
  links?: readonly InstructionLink[];
};

export type InstructionPlatform = {
  id: string;
  label: string;
  emoji: string;
  steps: InstructionStep[];
};

const IOS_KARING_APPSTORE_URL = "https://apps.apple.com/app/karing/id6472431552";
const KARING_GITHUB_URL = "https://github.com/KaringX/karing/releases";
const ANDROID_HAPP_PLAYSTORE_URL =
  "https://play.google.com/store/apps/details?id=com.happproxy";
const ANDROID_V2RAYTUNE_URL =
  "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru";

// Karing isn't on Google Play — the APK is self-hosted by nginx from /downloads/.
// deploy publishes /downloads/latest-android.json (version + real versioned
// filename); the Android step-1 APK link is rendered from this manifest so it
// always points at the latest build. Returns null if unreachable (the step then
// falls back to the GitHub/Happ/v2rayTune links).
export type AndroidApk = { url: string; version: string; filename: string };

export async function fetchAndroidApk(): Promise<AndroidApk | null> {
  try {
    const res = await fetch("/downloads/latest-android.json", { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<AndroidApk>;
    if (!data.url || !data.version) return null;
    return { url: data.url, version: data.version, filename: data.filename ?? "" };
  } catch {
    return null;
  }
}

// Karing Windows .exe раздаётся nginx из /downloads/ аналогично APK: деплой
// публикует /downloads/latest-windows.json (version + реальное версионируемое
// имя файла). Шаг 1 для Windows рендерит ссылку из этого манифеста, чтобы она
// всегда указывала на свежий билд. null, если манифест недоступен (шаг тогда
// откатывается на ссылку GitHub).
export type WindowsExe = { url: string; version: string; filename: string };

export async function fetchWindowsExe(): Promise<WindowsExe | null> {
  try {
    const res = await fetch("/downloads/latest-windows.json", { cache: "no-store" });
    if (!res.ok) return null;
    const data = (await res.json()) as Partial<WindowsExe>;
    if (!data.url || !data.version) return null;
    return { url: data.url, version: data.version, filename: data.filename ?? "" };
  } catch {
    return null;
  }
}

// Общие шаги для платформ на Karing (Windows / Android / iPhone / Mac).
const karingOpenStep: InstructionStep = {
  title: "Откройте приложение",
  description:
    "Запустите Karing, нажмите «Accept and continue», выберите русский язык и пройдите первичную настройку («Дальше» → «Дальше» → «Готово»).",
};

const importSubscriptionStep: InstructionStep = {
  title: "Добавьте подписку",
  description:
    "В Личном кабинете откройте «Ключи» и скопируйте ссылку подписки. В Karing выберите импорт подписки и вставьте эту ссылку — все серверы подтянутся автоматически.",
};

const enableVpnStep: InstructionStep = {
  title: "Включите VPN",
  description:
    "Нажмите кнопку со щитом в Karing — приложение подключится к ближайшему подходящему серверу.",
};

const doneStep: InstructionStep = {
  title: "Готово",
  description:
    "VPN работает. Не закрывайте Karing во время использования. Если соединение пропало — перевыпустите или обновите ключи в Личном кабинете.",
};

export const connectionPlatforms: readonly InstructionPlatform[] = [
  {
    id: "win",
    label: "Windows",
    emoji: "🖥",
    steps: [
      {
        title: "Установите Karing",
        description: "Скачайте и установите приложение Karing для Windows.",
        links: [{ href: KARING_GITHUB_URL, label: "Скачать Karing (GitHub)" }],
      },
      karingOpenStep,
      importSubscriptionStep,
      enableVpnStep,
      doneStep,
    ],
  },
  {
    id: "android",
    label: "Android",
    emoji: "🤖",
    steps: [
      {
        title: "Установите приложение",
        description:
          "Подойдёт Karing, Happ или v2rayTune — выберите любое. Рекомендуем Karing.",
        links: [
          { href: KARING_GITHUB_URL, label: "Karing (GitHub)" },
          { href: ANDROID_HAPP_PLAYSTORE_URL, label: "Happ" },
          { href: ANDROID_V2RAYTUNE_URL, label: "v2rayTune" },
        ],
      },
      karingOpenStep,
      importSubscriptionStep,
      enableVpnStep,
      doneStep,
    ],
  },
  {
    id: "ios",
    label: "iPhone",
    emoji: "📱",
    steps: [
      {
        title: "Установите Karing",
        description: "Скачайте и установите приложение Karing из App Store.",
        links: [{ href: IOS_KARING_APPSTORE_URL, label: "Скачать Karing" }],
      },
      karingOpenStep,
      importSubscriptionStep,
      enableVpnStep,
      doneStep,
    ],
  },
  {
    id: "mac",
    label: "Mac",
    emoji: "💻",
    steps: [
      {
        title: "Установите Karing",
        description: "Скачайте и установите приложение Karing из App Store.",
        links: [{ href: IOS_KARING_APPSTORE_URL, label: "Скачать Karing" }],
      },
      karingOpenStep,
      importSubscriptionStep,
      enableVpnStep,
      doneStep,
    ],
  },
  {
    id: "tv",
    label: "Телевизор",
    emoji: "📺",
    steps: [
      {
        title: "Установите Happ",
        description:
          "Только для телевизоров на Android TV. Установите приложение Happ Proxy Utility из Play Store.",
        links: [
          {
            href: ANDROID_HAPP_PLAYSTORE_URL,
            label: "Скачать Happ Proxy Utility",
          },
        ],
      },
      {
        title: "Откройте Happ",
        description: "Запустите приложение и нажмите кнопку добавления подписки.",
      },
      {
        title: "Скопируйте ссылку подписки",
        description:
          "В Личном кабинете откройте «Ключи» и скопируйте ссылку подписки. В Happ вставьте ссылку или отсканируйте QR-код.",
      },
      {
        title: "Отправьте данные",
        description:
          "Вставьте скопированную ссылку и нажмите «Отправить» — на телевизоре появится список ключей.",
      },
      {
        title: "Выберите сервер и включите",
        description:
          "Выберите сервер слева (например, «YouTube NoAds» для YouTube) и включайте/выключайте кнопкой справа.",
      },
    ],
  },
];
