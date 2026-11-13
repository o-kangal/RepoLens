import { prisma } from "./db";
import { NotFoundError, Logger as Log } from "./errors";
import * as path from "node:path";

const LIMIT = 10;

/** Loads users from the database. */
@Injectable()
export class UserService {
  private readonly log = new Log();

  // Cached for a minute.
  @Cached()
  async findById(id: string) {
    const user = await prisma.user.find(id, LIMIT);
    if (!user) throw new NotFoundError(id);
    return user;
  }

  handle = (name: string) => path.join(name);
}

export const slugify = (text: string): string =>
  text.toLowerCase().replace(/\s+/g, "-");

export function add(a: number, b: number) {
  return a + b;
}
