import { Amplify } from "aws-amplify";

interface RawEnv {
  VITE_USER_POOL_ID?: string;
  VITE_USER_POOL_CLIENT_ID?: string;
  VITE_COGNITO_DOMAIN?: string;
  VITE_REDIRECT_SIGN_IN?: string;
  VITE_REDIRECT_SIGN_OUT?: string;
}

function read(name: keyof RawEnv): string | undefined {
  const env = import.meta.env as unknown as RawEnv;
  const value = env[name];
  if (typeof value !== "string" || value.length === 0) return undefined;
  return value;
}

export function configureAmplify(): void {
  const userPoolId = read("VITE_USER_POOL_ID");
  const userPoolClientId = read("VITE_USER_POOL_CLIENT_ID");
  const domain = read("VITE_COGNITO_DOMAIN");
  const redirectSignIn = read("VITE_REDIRECT_SIGN_IN");
  const redirectSignOut = read("VITE_REDIRECT_SIGN_OUT");

  if (
    !userPoolId ||
    !userPoolClientId ||
    !domain ||
    !redirectSignIn ||
    !redirectSignOut
  ) {
    // Surface a console-only warning so local dev without a populated
    // .env.local still loads the SPA shell. The app behavior at runtime
    // (sign-in attempts) will fail until the values are present.
    console.warn(
      "Amplify Auth env vars missing. Populate web/.env.local from " +
        "`terraform output` before signing in.",
    );
    return;
  }

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId,
        userPoolClientId,
        loginWith: {
          oauth: {
            domain,
            scopes: ["openid", "email", "profile"],
            redirectSignIn: [redirectSignIn],
            redirectSignOut: [redirectSignOut],
            responseType: "code",
          },
        },
      },
    },
  });
}
