package com.demo;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import org.jboss.logging.Logger;

import java.util.Map;
import java.util.Random;

@Path("/api/inventory")
public class InventoryResource {

    private static final Logger LOG = Logger.getLogger(InventoryResource.class);
    private static final Random RANDOM = new Random();

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public Response getInventory() {
        LOG.info("GET /api/inventory called");

        if (RANDOM.nextDouble() < 0.40) {
            LOG.error("Inventory service temporarily unavailable - stock sync in progress");
            return Response.status(Response.Status.SERVICE_UNAVAILABLE)
                    .entity(Map.of(
                            "error", "SERVICE_UNAVAILABLE",
                            "message", "Inventory stock sync in progress. Try again later.",
                            "retryAfter", 30
                    ))
                    .build();
        }

        return Response.ok(Map.of(
                "Widget A", Map.of("stock", 142, "reserved", 12, "available", 130),
                "Gadget B", Map.of("stock", 38, "reserved", 5, "available", 33),
                "Doohickey C", Map.of("stock", 500, "reserved", 0, "available", 500)
        )).build();
    }
}
