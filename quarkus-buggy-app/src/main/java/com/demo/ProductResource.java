package com.demo;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import org.jboss.logging.Logger;

import java.util.List;
import java.util.Map;
import java.util.Random;

@Path("/api/products")
public class ProductResource {

    private static final Logger LOG = Logger.getLogger(ProductResource.class);
    private static final Random RANDOM = new Random();

    private static final List<Map<String, Object>> PRODUCT_CATALOG = List.of(
            Map.of("id", 1, "name", "Widget A", "price", 9.99),
            Map.of("id", 2, "name", "Gadget B", "price", 24.99),
            Map.of("id", 3, "name", "Doohickey C", "price", 4.99)
    );

    @GET
    @Produces(MediaType.APPLICATION_JSON)
    public Response getProducts() {
        LOG.info("GET /api/products called");

        if (RANDOM.nextDouble() < 0.30) {
            // Simulate uninitialised field / missing dependency
            LOG.error("NullPointerException while loading product catalog");
            String broken = null;
            broken.length(); // intentional NPE
        }

        return Response.ok(PRODUCT_CATALOG).build();
    }
}
