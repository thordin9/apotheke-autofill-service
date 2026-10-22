declare module "cloudflare:workers" {
  interface ProvidedEnv extends Env {}
}

declare module "*.toml?raw" {
  const content: string;
  export default content;
}
