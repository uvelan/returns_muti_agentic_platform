import { z } from "zod";

const envSchema = z
  .object({
    MODE: z.string().min(1),
    DEV: z.boolean(),
    PROD: z.boolean(),

    // Add browser-visible variables here using the VITE_ prefix.
    // Never expose passwords, credentials, or private connection strings.
    //
    // Example:
    // VITE_FEATURE_FLAG_X: z.enum(["true", "false"])
    //   .transform((value) => value === "true")
    //   .default("false"),
  })
  .readonly();

const parsedEnvironment = envSchema.safeParse(import.meta.env);

if (!parsedEnvironment.success) {
  console.error(
    "Invalid frontend environment configuration.",
    parsedEnvironment.error.flatten().fieldErrors,
  );

  throw new Error(
    "Frontend environment validation failed.",
  );
}

export const env = parsedEnvironment.data;

export type FrontendEnvironment = z.infer<
  typeof envSchema
>;