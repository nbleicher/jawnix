import { createServer } from "node:http";
import { handler } from "./handler.js";
createServer(handler).listen(Number(process.env.PORT || 3001), "127.0.0.1");
