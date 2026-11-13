export function ok(): number {
  return 1;
}

export class Broken {
  method( {
    return 2;
  }
}}}

const later = () => "still parsed";
