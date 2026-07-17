/**
 * Пошаговые инструкции по подключению.
 *
 * Контент зеркалирует шаги Telegram-бота (функции win_step / android_step /
 * ios_step / mac_step / tv_step и ветки *_happ_step в
 * backend/src/app/bot_transport/storefront_ui.py), адаптирован под веб.
 * Ссылки на магазины приложений и GitHub взяты из бота (проверенные). Ссылка
 * подписки пользователем копируется в Личном кабинете → Ключи.
 *
 * Для iOS/Android/Mac есть развилка приложения: Happ (рекомендуется — в нём
 * работает обход белых списков) и Karing. v2rayTune едет внутри ветки Happ как
 * альтернативное приложение для скачивания (отдельной развилки нет, инструкция
 * общая). Windows и Smart TV — без развилки (Windows — Karing, TV — Happ).
 */

export type InstructionLink = { href: string; label: string };

export type InstructionStep = {
  title: string;
  description: string;
  /** Кнопки-ссылки для шага (скачать приложение и т.п.). */
  links?: readonly InstructionLink[];
};

export type InstructionApp = {
  id: string;
  label: string;
  emoji?: string;
  recommended?: boolean;
  steps: InstructionStep[];
};

export type InstructionPlatform = {
  id: string;
  label: string;
  emoji: string;
  /** Одно приложение на платформе (win, tv). */
  steps?: InstructionStep[];
  /** Платформа с развилкой приложения — переключатель Happ/Karing (ios, android, mac). */
  apps?: InstructionApp[];
};

const IOS_KARING_APPSTORE_URL = "https://apps.apple.com/app/karing/id6472431552";
const KARING_GITHUB_URL = "https://github.com/KaringX/karing/releases";
const ANDROID_HAPP_PLAYSTORE_URL =
  "https://play.google.com/store/apps/details?id=com.happproxy";
const ANDROID_V2RAYTUNE_URL =
  "https://play.google.com/store/apps/details?id=com.v2raytun.android&hl=ru";
const IOS_HAPP_APPSTORE_URL = "https://apps.apple.com/app/happ-proxy-utility/id6504287215";
const IOS_V2RAYTUNE_URL = "https://apps.apple.com/app/v2raytun/id6476628951";

// Karing isn't on Google Play — the APK is self-hosted by nginx from /downloads/.
// deploy publishes /downloads/latest-android.json (version + real versioned
// filename); the Android step-1 APK link is rendered from this manifest so it
// always points at the latest build. Returns null if unreachable (the step then
// falls back to the GitHub link).
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

// Общие шаги для ветки Karing (Windows / Android / iPhone / Mac).
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

// Общие шаги для ветки Happ (обход белых списков; v2rayTune — альтернатива при
// скачивании, но обход работает только в Happ).
const happOpenStep: InstructionStep = {
  title: "Откройте приложение",
  description:
    "Запустите Happ, примите условия и выдайте разрешения. Откройте раздел «Подписки».",
};

const happImportStep: InstructionStep = {
  title: "Добавьте подписку",
  description:
    "В Личном кабинете откройте «Ключи» и скопируйте ссылку подписки. В Happ нажмите «Добавить подписку» и вставьте ссылку — серверы с обходом белых списков подтянутся автоматически.",
};

const happConnectStep: InstructionStep = {
  title: "Подключитесь",
  description:
    "Выберите обходной сервер и нажмите кнопку подключения. VPN остаётся стабильным даже при жёстких блокировках провайдера.",
};

const happDoneStep: InstructionStep = {
  title: "Готово",
  description:
    "VPN настроен. Не закрывайте Happ во время использования. Если соединение пропало — переподключитесь, выбрав другой сервер.",
};

// Ветка Happ для платформы (ios/mac/android). differ только ссылками скачивания.
function happApp(downloadLinks: readonly InstructionLink[]): InstructionApp {
  return {
    id: "happ",
    label: "Happ",
    emoji: "🚀",
    recommended: true,
    steps: [
      {
        title: "Установите Happ или v2rayTune",
        description:
          "Скачайте приложение. Рекомендуем Happ — в нём работает обход белых списков. v2rayTune подойдёт как альтернатива.",
        links: downloadLinks,
      },
      happOpenStep,
      happImportStep,
      happConnectStep,
      happDoneStep,
    ],
  };
}

// Ветка Karing для платформы (ios/mac/android).
function karingApp(installTitle: string, installDesc: string, installLinks: readonly InstructionLink[]): InstructionApp {
  return {
    id: "karing",
    label: "Karing",
    emoji: "📱",
    steps: [
      { title: installTitle, description: installDesc, links: installLinks },
      karingOpenStep,
      importSubscriptionStep,
      enableVpnStep,
      doneStep,
    ],
  };
}

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
    apps: [
      happApp([
        { href: ANDROID_HAPP_PLAYSTORE_URL, label: "Скачать Happ" },
        { href: ANDROID_V2RAYTUNE_URL, label: "Скачать v2rayTune" },
      ]),
      karingApp(
        "Установите Karing",
        "Скачайте и установите приложение Karing.",
        [{ href: KARING_GITHUB_URL, label: "Karing (GitHub)" }],
      ),
    ],
  },
  {
    id: "ios",
    label: "iPhone",
    emoji: "📱",
    apps: [
      happApp([
        { href: IOS_HAPP_APPSTORE_URL, label: "Скачать Happ" },
        { href: IOS_V2RAYTUNE_URL, label: "Скачать v2rayTune" },
      ]),
      karingApp(
        "Установите Karing",
        "Скачайте и установите приложение Karing из App Store.",
        [{ href: IOS_KARING_APPSTORE_URL, label: "Скачать Karing" }],
      ),
    ],
  },
  {
    id: "mac",
    label: "Mac",
    emoji: "💻",
    apps: [
      happApp([
        { href: IOS_HAPP_APPSTORE_URL, label: "Скачать Happ" },
        { href: IOS_V2RAYTUNE_URL, label: "Скачать v2rayTune" },
      ]),
      karingApp(
        "Установите Karing",
        "Скачайте и установите приложение Karing из App Store.",
        [{ href: IOS_KARING_APPSTORE_URL, label: "Скачать Karing" }],
      ),
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
